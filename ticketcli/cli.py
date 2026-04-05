from __future__ import annotations

import argparse
import json as json_module
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import argcomplete

from ticketcli.cli_common import coerce_issue_ref, ensure_output_dir, resolve_runtime
from ticketcli.completion_cache import invalidate_all, invalidate_cache, load_cache, save_cache
from ticketcli.config import ConfigError, load_config, load_targets, load_user_mappings, resolve_target, save_targets, set_default_target
from ticketcli.formatting import render_issue, _human_date
from ticketcli.handler_factory import build_handler
from ticketcli.utils.editor import open_editor
from ticketcli.utils.interactive import choose_indices_interactively


HandlerFn = Callable[[argparse.Namespace], None]
UNCHANGED = object()

ISSUE_CACHE_TTL_SECONDS = 24 * 60 * 60
USER_CACHE_TTL_SECONDS = 24 * 60 * 60
TARGET_CACHE_TTL_SECONDS = 24 * 60 * 60
TRANSITION_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

# Statuses that indicate the issue hasn't been started yet.
_IDLE_STATUSES = frozenset({
    "open", "to do", "todo", "new", "planned", "backlog",
    "created", "not started", "ready", "pending",
})


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip()


def _values_equal(left: str | None, right: str | None) -> bool:
    return _normalize_text(left) == _normalize_text(right)


def _safe_startswith(values: list[str], prefix: str) -> list[str]:
    return [value for value in values if value.startswith(prefix)]


def _filter_mapping_prefix(values: dict[str, str], prefix: str) -> dict[str, str]:
    return {key: desc for key, desc in values.items() if key.startswith(prefix)}


def _stringify(value) -> str:
    return "" if value is None else str(value).strip()


def _completion_target_name(parsed_args) -> str:
    value = getattr(parsed_args, "target", None)
    if value:
        return str(value)

    try:
        config = load_config()
    except Exception:
        return "default"

    default_target = config.get("default_target")
    return str(default_target or "default")


def _issue_cache_key(parsed_args) -> str:
    target_name = _completion_target_name(parsed_args)
    return f"{target_name}__all_unresolved"


def _user_cache_key(parsed_args) -> str:
    target_name = _completion_target_name(parsed_args)
    return f"{target_name}__users"


def _invalidate_target_completion_caches(target_name: str | None) -> None:
    if not target_name:
        return

    invalidate_cache("issues", f"{target_name}__all_unresolved")
    invalidate_cache("users", f"{target_name}__users")
    invalidate_cache("labels", f"{target_name}__labels")
    invalidate_cache("components", f"{target_name}__components")


def _transition_cache_key(target_name: str) -> str:
    return f"{target_name}__transitions"


def _get_cached_transitions(target_name: str) -> list[str] | None:
    cached = load_cache("transitions", _transition_cache_key(target_name), TRANSITION_CACHE_TTL_SECONDS)
    if isinstance(cached, list):
        return [str(x) for x in cached]
    return None


def _cache_transitions(target_name: str, transitions: list[str]) -> None:
    save_cache("transitions", _transition_cache_key(target_name), transitions)


# Common status names that mean "closed / done".
_CLOSED_STATUSES = frozenset({
    "done", "closed", "resolved", "complete", "completed",
})


def _resolve_close_status(handler, target_name: str, issue_key: str, explicit: str | None = None) -> str:
    """Return the correct close-status for *target_name*, caching the result.

    If *explicit* is given (user passed ``--status``), use it directly.
    Otherwise look up cached preference, or auto-detect from available
    transitions and prompt if ambiguous.
    """
    if explicit:
        return explicit

    # Check cached close preference
    cache_key = f"{target_name}__close_status_pref"
    pref = load_cache("transitions", cache_key, TRANSITION_CACHE_TTL_SECONDS)
    if isinstance(pref, str) and pref:
        return pref

    # Fetch transitions (use the transitions cache when available)
    transitions = _get_cached_transitions(target_name)
    if transitions is None:
        try:
            transitions = handler.list_transitions(issue_key)
        except Exception:
            transitions = []
        if transitions:
            _cache_transitions(target_name, transitions)

    if not transitions:
        return "Done"  # fallback

    # Auto-detect: match common closed-status names
    candidates = [t for t in transitions if t.lower() in _CLOSED_STATUSES]

    if len(candidates) == 1:
        save_cache("transitions", cache_key, candidates[0])
        return candidates[0]

    # Multiple or zero matches — ask the user
    choices = candidates if candidates else transitions
    if len(choices) == 1:
        save_cache("transitions", cache_key, choices[0])
        return choices[0]

    print("  Available statuses:")
    for idx, name in enumerate(choices, 1):
        print(f"    {idx}. {name}")
    raw = input("  Pick the 'close' status [number, or Enter to use 'Done']: ").strip()
    if raw:
        try:
            choice = int(raw)
            if 1 <= choice <= len(choices):
                selected = choices[choice - 1]
                save_cache("transitions", cache_key, selected)
                return selected
        except ValueError:
            pass
    return "Done"


def _pick_in_progress_status(transitions: list[str]) -> str | None:
    """Auto-detect which transition represents 'in progress'.

    If exactly one candidate matches common in-progress names, return it
    automatically.  If multiple match, prompt the user to pick one.
    """
    in_progress_keywords = {"in progress", "active", "in development", "doing", "started", "working"}
    candidates = [t for t in transitions if t.lower() in in_progress_keywords]

    if len(candidates) == 1:
        return candidates[0]

    if not candidates:
        # No obvious match — let the user pick from all available transitions
        candidates = transitions

    if len(candidates) == 1:
        return candidates[0]

    # Multiple candidates — ask user
    print("  Available statuses:")
    for idx, name in enumerate(candidates, 1):
        print(f"    {idx}. {name}")
    raw = input("  Pick the 'in progress' status [number, or Enter to skip]: ").strip()
    if not raw:
        return None
    try:
        choice = int(raw)
        if 1 <= choice <= len(candidates):
            return candidates[choice - 1]
    except ValueError:
        pass
    return None


def _maybe_suggest_in_progress(handler, target_name: str, issue_key: str, current_status: str | None) -> None:
    """If the issue is in an idle status, suggest transitioning to in-progress."""
    if not current_status:
        return
    if current_status.lower() not in _IDLE_STATUSES:
        return

    # Check cached transitions first, fetch and cache if needed
    transitions = _get_cached_transitions(target_name)
    if transitions is None:
        try:
            transitions = handler.list_transitions(issue_key)
        except Exception:
            return
        if transitions:
            _cache_transitions(target_name, transitions)

    if not transitions:
        return

    # Check if we have a cached in-progress preference for this target
    pref = load_cache("transitions", f"{target_name}__in_progress_pref", TRANSITION_CACHE_TTL_SECONDS)
    if isinstance(pref, str) and pref in transitions:
        in_progress_name = pref
    else:
        in_progress_name = _pick_in_progress_status(transitions)
        if in_progress_name:
            save_cache("transitions", f"{target_name}__in_progress_pref", in_progress_name)

    if not in_progress_name:
        return

    reply = input(f"  Transition {issue_key} to '{in_progress_name}'? [Y/n] ").strip().lower()
    if reply in ("", "y", "yes"):
        try:
            handler.transition_issue(issue_key, in_progress_name)
            print(f"  ➜ {issue_key} is now '{in_progress_name}'")
        except Exception as exc:
            print(f"  Could not transition: {exc}")


def _target_names() -> list[str]:
    cached = load_cache("targets", "all", TARGET_CACHE_TTL_SECONDS)
    if isinstance(cached, list):
        return [str(x) for x in cached]

    try:
        names = sorted(load_targets().keys())
    except Exception:
        return []

    save_cache("targets", "all", names)
    return names


def target_completer(prefix: str, **kwargs) -> list[str]:
    return _safe_startswith(_target_names(), prefix)


def _resolve_handler_for_completion(parsed_args):
    try:
        _, _, _, handler = resolve_runtime(parsed_args)
        return handler
    except Exception:
        return None


def _extract_issue_key(issue) -> str | None:
    if isinstance(issue, str):
        return issue
    if isinstance(issue, dict):
        return issue.get("key")
    return getattr(issue, "key", None)


def _extract_issue_summary(issue) -> str | None:
    if isinstance(issue, dict):
        return issue.get("summary")
    return getattr(issue, "summary", None)


def _extract_issue_assignee(issue) -> str | None:
    if isinstance(issue, dict):
        return issue.get("assignee")
    return getattr(issue, "assignee", None)


def _serialize_issues_for_cache(issues) -> list[dict[str, str]]:
    serialized: list[dict[str, str]] = []
    for issue in issues:
        key = _extract_issue_key(issue)
        if not key:
            continue
        labels_raw = getattr(issue, "labels", None) or (issue.get("labels") if isinstance(issue, dict) else None) or []
        comps_raw = getattr(issue, "components", None) or (issue.get("components") if isinstance(issue, dict) else None) or []
        serialized.append(
            {
                "key": key,
                "summary": _stringify(_extract_issue_summary(issue)),
                "assignee": _stringify(_extract_issue_assignee(issue)),
                "labels": [str(l) for l in labels_raw],
                "components": [str(c) for c in comps_raw],
            }
        )
    return serialized


def _list_issues_for_completion(parsed_args) -> list[dict[str, str]]:
    cache_key = _issue_cache_key(parsed_args)
    cached = load_cache("issues", cache_key, ISSUE_CACHE_TTL_SECONDS)
    if isinstance(cached, list):
        return [item for item in cached if isinstance(item, dict)]

    handler = _resolve_handler_for_completion(parsed_args)
    if handler is None:
        return []

    try:
        # Always fetch ALL unresolved issues for completion regardless of
        # the current command's flags, so the autocomplete pool is complete.
        issues = handler.list_issues(created_by_me=False, all_unresolved=True)
    except TypeError:
        try:
            issues = handler.list_issues()
        except Exception:
            return []
    except Exception:
        return []

    serialized = _serialize_issues_for_cache(issues)
    save_cache("issues", cache_key, serialized)
    return serialized


def issue_completer(prefix: str, parsed_args, **kwargs) -> dict[str, str] | list[str]:
    issues = _list_issues_for_completion(parsed_args)
    completions: dict[str, str] = {}

    for issue in issues:
        key = _stringify(issue.get("key"))
        if not key or not key.startswith(prefix):
            continue

        summary = _stringify(issue.get("summary"))
        completions[key] = summary or ""

    # When the typed prefix exactly matches a single issue key, return it
    # without a description so argcomplete doesn't append the summary to
    # the final value.
    upper_prefix = prefix.upper()
    if upper_prefix in {k.upper() for k in completions} and len(completions) == 1:
        return list(completions.keys())

    return completions


def _candidate_users_from_handler(handler) -> dict[str, str]:
    for attr_name in ("list_users", "available_user_mappings", "get_assignable_users"):
        fn = getattr(handler, attr_name, None)
        if not callable(fn):
            continue

        try:
            raw = fn()
        except Exception:
            continue

        users: dict[str, str] = {}
        for item in raw or []:
            if isinstance(item, str):
                users[item] = "user"
                continue

            if isinstance(item, dict):
                username = (
                    item.get("username")
                    or item.get("name")
                    or item.get("email")
                    or item.get("id")
                )
                if username:
                    desc = (
                        item.get("display_name")
                        or item.get("full_name")
                        or item.get("email")
                        or item.get("id")
                        or "user"
                    )
                    users[str(username)] = str(desc)
                continue

            username = None
            for key in ("username", "name", "email", "id"):
                value = getattr(item, key, None)
                if value:
                    username = str(value)
                    break

            if username:
                desc = (
                    getattr(item, "display_name", None)
                    or getattr(item, "full_name", None)
                    or getattr(item, "email", None)
                    or getattr(item, "id", None)
                    or "user"
                )
                users[username] = str(desc)

        if users:
            return dict(sorted(users.items()))
    return {}


def _list_users_for_completion(parsed_args) -> dict[str, str]:
    cache_key = _user_cache_key(parsed_args)
    cached = load_cache("users", cache_key, USER_CACHE_TTL_SECONDS)
    if isinstance(cached, dict):
        return {str(k): str(v) for k, v in cached.items()}

    handler = _resolve_handler_for_completion(parsed_args)
    if handler is None:
        return {}

    users = _candidate_users_from_handler(handler)
    if users:
        save_cache("users", cache_key, users)
    return users


def assignee_completer(prefix: str, parsed_args, **kwargs) -> dict[str, str]:
    users = _list_users_for_completion(parsed_args)
    return _filter_mapping_prefix(users, prefix)


LABEL_CACHE_TTL_SECONDS = 24 * 60 * 60
COMPONENT_CACHE_TTL_SECONDS = 48 * 60 * 60  # components change rarely


def _label_cache_key(parsed_args) -> str:
    target_name = _completion_target_name(parsed_args)
    return f"{target_name}__labels"


def _component_cache_key(parsed_args) -> str:
    target_name = _completion_target_name(parsed_args)
    return f"{target_name}__components"


def _list_labels_for_completion(parsed_args) -> list[str]:
    cache_key = _label_cache_key(parsed_args)
    cached = load_cache("labels", cache_key, LABEL_CACHE_TTL_SECONDS)
    if isinstance(cached, list):
        return [str(x) for x in cached]

    # Gather unique labels from recent issues
    issues = _list_issues_for_completion(parsed_args)
    labels_set: set[str] = set()
    for issue in issues:
        for lbl in issue.get("labels") or []:
            if lbl:
                labels_set.add(str(lbl))
    labels = sorted(labels_set)
    if labels:
        save_cache("labels", cache_key, labels)
    return labels


def _list_components_for_completion(parsed_args) -> list[str]:
    cache_key = _component_cache_key(parsed_args)
    cached = load_cache("components", cache_key, COMPONENT_CACHE_TTL_SECONDS)
    if isinstance(cached, list):
        return [str(x) for x in cached]

    # Gather unique components from recent issues
    issues = _list_issues_for_completion(parsed_args)
    comp_set: set[str] = set()
    for issue in issues:
        for comp in issue.get("components") or []:
            if comp:
                comp_set.add(str(comp))
    components = sorted(comp_set)
    if components:
        save_cache("components", cache_key, components)
    return components


def label_completer(prefix: str, parsed_args, **kwargs) -> list[str]:
    return _safe_startswith(_list_labels_for_completion(parsed_args), prefix)


def component_completer(prefix: str, parsed_args, **kwargs) -> list[str]:
    return _safe_startswith(_list_components_for_completion(parsed_args), prefix)


def _add_target_argument(parser: argparse.ArgumentParser):
    arg = parser.add_argument("-t", "--target", help="Target name")
    arg.completer = target_completer
    return arg


def _add_issue_argument(parser: argparse.ArgumentParser, required: bool = True):
    arg = parser.add_argument("-i", "--issue", required=required, help="Issue number or full key")
    arg.completer = issue_completer
    return arg


def _add_assignee_argument(parser: argparse.ArgumentParser):
    arg = parser.add_argument(
        "-a",
        "--assignee",
        nargs="?",
        const="",
        help="Set assignee; with no value, open interactive selection",
    )
    arg.completer = assignee_completer
    return arg


def _resolve_edit_assignee(
    handler,
    assignee_arg: str | None,
    interactive: bool,
    unassign: bool,
):
    if unassign:
        return None
    if interactive:
        selected = handler.resolve_assignee(None)
        return UNCHANGED if selected is None else selected
    if assignee_arg is not None:
        return handler.resolve_assignee(assignee_arg)
    return UNCHANGED


def _run_add(args: argparse.Namespace) -> None:
    config, target_name, _, handler = resolve_runtime(args)
    summary = args.summary or input("Summary: ").strip()

    if args.edit:
        initial = args.description or "# Write issue description below\n"
        description = open_editor(config["editor"], initial)
    else:
        description = args.description or input("Description: ").strip()

    assignee = handler.resolve_assignee(args.assignee)
    labels = args.labels or None
    components = args.components or None
    issue = handler.create_issue(
        summary=summary,
        description=description,
        assignee=assignee,
        labels=labels,
        components=components,
    )
    _invalidate_target_completion_caches(target_name)
    print(f"Created issue in target '{target_name}': {issue.key}")


def _run_pin_interactive(handler, issue_key: str, issue) -> None:
    """Interactively pin/unpin comments on an issue."""
    comments = issue.comments
    if not comments:
        print("No comments on this issue.")
        return

    # Show last 5 comments + any currently pinned
    last_5 = comments[-5:]
    pinned_older = [c for c in comments[:-5] if c.pinned] if len(comments) > 5 else []
    display = pinned_older + last_5

    while True:
        print(f"\nComments for {issue_key}:")
        for idx, c in enumerate(display, 1):
            pin_marker = "📌 " if c.pinned else "   "
            date = _human_date(c.created_at)
            body_preview = c.body[:80].replace("\n", " ")
            if len(c.body) > 80:
                body_preview += "..."
            print(f"  {idx}. {pin_marker}[{date}] {c.author}: {body_preview}")

        raw = input("\nToggle pin by number (comma-separated), or Enter to finish: ").strip()
        if not raw:
            break

        try:
            indices = [int(x.strip()) for x in raw.split(",")]
        except ValueError:
            print("Invalid input. Use numbers like: 1,3")
            continue

        for n in indices:
            if n < 1 or n > len(display):
                print(f"  Skipping invalid number: {n}")
                continue
            comment = display[n - 1]
            try:
                if comment.pinned:
                    handler.unpin_comment(issue_key, comment.id)
                    comment.pinned = False
                    print(f"  Unpinned comment {n}")
                else:
                    handler.pin_comment(issue_key, comment.id)
                    comment.pinned = True
                    print(f"  Pinned comment {n}")
            except NotImplementedError:
                print(f"  Pin/unpin not supported by this backend.")
                return
            except Exception as exc:
                print(f"  Error toggling pin: {exc}")


def _run_edit(args: argparse.Namespace) -> None:
    config, target_name, _, handler = resolve_runtime(args)
    issue_key = coerce_issue_ref(handler, args.issue)
    current = handler.get_issue_details(issue_key)

    # --pin mode: interactive pin/unpin of comments
    if getattr(args, "pin", False):
        _run_pin_interactive(handler, issue_key, current)
        return

    new_summary = args.summary
    new_description = args.description

    if args.edit:
        new_description = open_editor(config["editor"], current.description or "")

    new_assignee = _resolve_edit_assignee(
        handler=handler,
        assignee_arg=getattr(args, "assignee", None),
        interactive=getattr(args, "assignee_interactive", False),
        unassign=getattr(args, "unassign", False),
    )

    changed_fields: dict[str, str | None] = {}

    if new_summary is not None and not _values_equal(new_summary, current.summary):
        changed_fields["summary"] = new_summary

    if new_description is not None and not _values_equal(new_description, current.description):
        changed_fields["description"] = new_description

    if new_assignee is not UNCHANGED and not _values_equal(new_assignee, current.assignee):
        changed_fields["assignee"] = new_assignee

    new_labels = getattr(args, "labels", None)
    if new_labels is not None:
        if set(new_labels) != set(current.labels):
            changed_fields["labels"] = new_labels

    new_components = getattr(args, "components", None)
    if new_components is not None:
        if set(new_components) != set(current.components):
            changed_fields["components"] = new_components

    if not changed_fields:
        print(f"No changes for issue: {issue_key}")
        return

    issue = handler.edit_issue(issue_key, **changed_fields)
    _invalidate_target_completion_caches(target_name)
    print(f"Updated issue: {issue.key}")


def _run_assign(args: argparse.Namespace) -> None:
    _, target_name, _, handler = resolve_runtime(args)
    issue_key = coerce_issue_ref(handler, args.issue)
    current = handler.get_issue_details(issue_key)

    assignee_arg = getattr(args, "assignee", None)
    assignee_interactive = getattr(args, "assignee_interactive", False)
    unassign = getattr(args, "unassign", False)

    interactive = (assignee_interactive or assignee_arg is None) and not unassign
    new_assignee = _resolve_edit_assignee(
        handler=handler,
        assignee_arg=assignee_arg,
        interactive=interactive,
        unassign=unassign,
    )

    if new_assignee is UNCHANGED or _values_equal(new_assignee, current.assignee):
        print(f"No changes for issue: {issue_key}")
        return

    issue = handler.edit_issue(issue_key, assignee=new_assignee)
    _invalidate_target_completion_caches(target_name)
    print(f"Updated issue: {issue.key}")


def _run_comment(args: argparse.Namespace) -> None:
    config, target_name, _, handler = resolve_runtime(args)
    issue_key = coerce_issue_ref(handler, args.issue)

    # Fetch issue once — reuse status for the in-progress suggestion
    issue = handler.get_issue_details(issue_key)

    if args.message is not None:
        comment = args.message
    else:
        comment = open_editor(config["editor"], "# Write comment below\n")

    comment = "\n".join(line for line in comment.splitlines() if not line.lstrip().startswith("#")).strip()
    if not comment:
        raise SystemExit("No comment provided.")

    handler.add_comment(issue_key, comment, author=handler.map_human_user(args.author))
    _invalidate_target_completion_caches(target_name)
    print(f"Comment added to {issue_key}")
    _maybe_suggest_in_progress(handler, target_name, issue_key, issue.status)


def _run_show(args: argparse.Namespace) -> None:
    _, target_name, _, handler = resolve_runtime(args)
    issue_key = coerce_issue_ref(handler, args.issue)
    issue = handler.get_issue_details(issue_key)
    if getattr(args, "format", "plain") == "json":
        print(json_module.dumps(issue.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_issue(issue, target_name=target_name))


def _run_attachments(args: argparse.Namespace) -> None:
    _, target_name, _, handler = resolve_runtime(args)
    issue_key = coerce_issue_ref(handler, args.issue)
    issue = handler.get_issue_details(issue_key)
    names = [f"{a.id}: {a.name}" for a in issue.attachments]
    indices = choose_indices_interactively(names, "Select attachments")
    selected_ids = [issue.attachments[i].id for i in indices]
    saved = handler.download_attachments(issue_key, selected_ids, ensure_output_dir(args.output_dir))
    if not saved:
        print("No attachments downloaded.")
        return
    print("Downloaded:")
    for path in saved:
        print(f"- {path}")
    _maybe_suggest_in_progress(handler, target_name, issue_key, issue.status)


def _run_list(args: argparse.Namespace) -> None:
    _, _, _, handler = resolve_runtime(args)
    issues = handler.list_issues(created_by_me=args.created, all_unresolved=args.all)
    if getattr(args, "format", "plain") == "json":
        rows = []
        for issue in issues:
            rows.append({
                "key": issue.key,
                "summary": issue.summary,
                "assignee": issue.assignee or None,
                "status": issue.status or None,
                "labels": list(issue.labels) if issue.labels else [],
                "components": list(issue.components) if issue.components else [],
            })
        print(json_module.dumps(rows, indent=2, ensure_ascii=False))
    else:
        for issue in issues:
            if args.created or args.all:
                assignee = issue.assignee or "-"
                print(f"{issue.key} | {assignee} | {issue.summary}")
            else:
                print(f"{issue.key} | {issue.summary}")


def _run_target(args: argparse.Namespace) -> None:
    require_explicit = None
    if args.require_target:
        require_explicit = True
    elif args.allow_default:
        require_explicit = False

    target_name = None
    if args.default_target:
        target_name = args.default_target
    elif args.clear_default:
        target_name = ""

    config = set_default_target(target_name=target_name, require_explicit_target=require_explicit)
    invalidate_cache("targets", "all")
    print("Updated target settings:")
    print(f"- default_target: {config.get('default_target')}")
    print(f"- require_explicit_target: {config.get('require_explicit_target')}")


def _run_targets(_: argparse.Namespace) -> None:
    config = load_config()
    targets = load_targets()
    if not targets:
        print("No targets configured yet.")
        return
    for name, target in targets.items():
        marker = " (default)" if config.get("default_target") == name else ""
        print(f"- {name}{marker}: ticket_system={target.get('ticket_system')} project_base={target.get('project_base')}")


def _run_migrate(args: argparse.Namespace) -> None:
    """Copy issue(s) from one target (source) to another (destination)."""
    # Resolve source
    source_target_name, source_target_config = resolve_target(args.source)
    source_mappings = load_user_mappings(source_target_name)
    source_handler = build_handler(source_target_name, source_target_config, source_mappings)

    # Resolve destination
    dest_target_name, dest_target_config = resolve_target(args.dest)
    dest_mappings = load_user_mappings(dest_target_name)
    dest_handler = build_handler(dest_target_name, dest_target_config, dest_mappings)

    dry_run = getattr(args, "dry_run", False)

    # Collect issues to migrate
    if getattr(args, "all", False):
        source_issues_list = source_handler.list_issues(all_unresolved=True)
        keys_to_migrate = [item.key for item in source_issues_list]
    else:
        keys_to_migrate = [coerce_issue_ref(source_handler, args.issue)]

    if not keys_to_migrate:
        print("No issues to migrate.")
        return

    for issue_key in keys_to_migrate:
        _migrate_one(
            source_handler=source_handler,
            dest_handler=dest_handler,
            source_target_name=source_target_name,
            dest_target_name=dest_target_name,
            issue_key=issue_key,
            copy_comments=args.comments,
            no_assignee=args.no_assignee,
            close_source=args.close_source,
            copy_attachments=getattr(args, "attachments", False),
            dry_run=dry_run,
        )

    if not dry_run:
        _invalidate_target_completion_caches(source_target_name)
        _invalidate_target_completion_caches(dest_target_name)
    print("Migration complete." if not dry_run else "Dry-run complete — no changes were made.")


def _migrate_one(
    *,
    source_handler,
    dest_handler,
    source_target_name: str,
    dest_target_name: str,
    issue_key: str,
    copy_comments: bool,
    no_assignee: bool,
    close_source: bool,
    copy_attachments: bool,
    dry_run: bool,
) -> None:
    import tempfile
    from pathlib import Path

    source_issue = source_handler.get_issue_details(issue_key)

    tag = "[DRY-RUN] " if dry_run else ""
    print(f"{tag}Migrating: {source_issue.key} — {source_issue.summary}")
    print(f"  From: {source_target_name} → To: {dest_target_name}")

    # Build kwargs for create_issue
    create_kwargs: dict = {}
    if source_issue.labels:
        create_kwargs["labels"] = source_issue.labels
    if source_issue.components:
        create_kwargs["components"] = source_issue.components

    # Try to map the assignee to the destination using human-readable names
    if source_issue.assignee and not no_assignee:
        # 1. Try direct mapping (assignee value is already a human alias)
        mapped = dest_handler.map_human_user(source_issue.assignee)
        if mapped:
            create_kwargs["assignee"] = mapped
        else:
            # 2. Reverse-lookup: source system ID → human alias → dest system ID
            human_name = source_handler.reverse_map_user(source_issue.assignee)
            if human_name:
                mapped = dest_handler.map_human_user(human_name)
                if mapped:
                    create_kwargs["assignee"] = mapped
            if "assignee" not in create_kwargs:
                # 3. Fallback: use raw value (may work on same-type backends)
                create_kwargs["assignee"] = source_issue.assignee

    if dry_run:
        print(f"  Would create issue: summary={source_issue.summary!r}")
        if create_kwargs.get("assignee"):
            print(f"  Would set assignee: {create_kwargs['assignee']}")
        if create_kwargs.get("labels"):
            print(f"  Would set labels: {', '.join(create_kwargs['labels'])}")
        if create_kwargs.get("components"):
            print(f"  Would set components: {', '.join(create_kwargs['components'])}")
        if copy_comments and source_issue.comments:
            print(f"  Would migrate {len(source_issue.comments)} comment(s).")
        if copy_attachments and source_issue.attachments:
            print(f"  Would migrate {len(source_issue.attachments)} attachment(s).")
        if close_source:
            print(f"  Would close source issue: {issue_key}")
        return

    new_issue = dest_handler.create_issue(
        summary=source_issue.summary,
        description=source_issue.description,
        **create_kwargs,
    )
    print(f"  Created: {new_issue.key}")

    if copy_comments and source_issue.comments:
        migrated_comments = 0
        for comment in source_issue.comments:
            header = f"[Migrated from {source_issue.key}] {comment.author} ({comment.created_at or '?'}):\n\n"
            dest_handler.add_comment(new_issue.key, header + comment.body)
            migrated_comments += 1
        print(f"  Migrated {migrated_comments} comment(s).")

    if copy_attachments and source_issue.attachments:
        migrated_attachments = 0
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            att_ids = [a.id for a in source_issue.attachments]
            try:
                downloaded = source_handler.download_attachments(issue_key, att_ids, tmp_path)
            except Exception as exc:
                print(f"  Warning: could not download attachments: {exc}")
                downloaded = []
            for file_path in downloaded:
                try:
                    dest_handler.upload_attachment(new_issue.key, file_path)
                    migrated_attachments += 1
                except Exception as exc:
                    print(f"  Warning: could not upload {file_path.name}: {exc}")
        if migrated_attachments:
            print(f"  Migrated {migrated_attachments} attachment(s).")

    if close_source:
        close_status = _resolve_close_status(source_handler, source_target_name, issue_key)
        try:
            source_handler.transition_issue(issue_key, close_status)
            print(f"  Closed source issue: {issue_key}")
        except Exception:
            try:
                source_handler.edit_issue(issue_key, status=close_status)
                print(f"  Closed source issue: {issue_key}")
            except Exception:
                print(f"  Could not auto-close source issue {issue_key} (may need manual transition).")


def _run_cache(args: argparse.Namespace) -> None:
    action = getattr(args, "cache_action", None)
    if action == "clear":
        count = invalidate_all()
        print(f"Cleared {count} cached file(s).")
    else:
        raise SystemExit("Unknown cache action. Use: ticketcli cache clear")


def _run_upload(args: argparse.Namespace) -> None:
    from pathlib import Path

    _, target_name, _, handler = resolve_runtime(args)
    issue_key = coerce_issue_ref(handler, args.issue)
    file_path = Path(args.file)
    if not file_path.is_file():
        raise SystemExit(f"File not found: {file_path}")

    # Fetch issue status before uploading (reused for in-progress suggestion)
    issue = handler.get_issue_details(issue_key)

    handler.upload_attachment(issue_key, file_path)
    _invalidate_target_completion_caches(target_name)
    print(f"Uploaded {file_path.name} to {issue_key}")
    _maybe_suggest_in_progress(handler, target_name, issue_key, issue.status)


def _run_delete_attachment(args: argparse.Namespace) -> None:
    _, target_name, _, handler = resolve_runtime(args)
    issue_key = coerce_issue_ref(handler, args.issue)
    attachment_id = args.attachment_id
    handler.delete_attachment(issue_key, attachment_id)
    _invalidate_target_completion_caches(target_name)
    print(f"Deleted attachment {attachment_id} from {issue_key}")


def _run_status(args: argparse.Namespace) -> None:
    _, target_name, _, handler = resolve_runtime(args)
    issue_key = coerce_issue_ref(handler, args.issue)
    handler.transition_issue(issue_key, args.status)
    _invalidate_target_completion_caches(target_name)
    print(f"Transitioned {issue_key} to '{args.status}'")


def _run_close(args: argparse.Namespace) -> None:
    _, target_name, _, handler = resolve_runtime(args)
    issue_key = coerce_issue_ref(handler, args.issue)
    target_status = _resolve_close_status(handler, target_name, issue_key, args.status)
    handler.transition_issue(issue_key, target_status)
    _invalidate_target_completion_caches(target_name)
    print(f"Closed {issue_key} (status: '{target_status}')")


# ---------------------------------------------------------------------------
# Jira-notation duration helpers
# ---------------------------------------------------------------------------

_JIRA_DURATION_UNITS = {"w": 5 * 8, "d": 8, "h": 1}  # all in hours


def _parse_jira_duration(text: str) -> float:
    """Parse '1w', '2d', '8h', '1w2d4h' → total hours."""
    total = 0.0
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*([wdh])", text.lower()):
        total += float(match.group(1)) * _JIRA_DURATION_UNITS[match.group(2)]
    if total == 0:
        raise ValueError(f"Cannot parse duration: {text!r}")
    return total


def _format_hours(hours: float) -> str:
    if hours >= 8:
        d, remainder = divmod(hours, 8)
        if remainder:
            return f"{int(d)}d {remainder:.0f}h"
        return f"{int(d)}d"
    return f"{hours:.1f}h"


def _parse_day_name(name: str) -> int:
    """Convert day name to weekday number (0=Monday)."""
    names = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
             "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    return names.get(name.lower().strip(), 0)


def _compute_cycle_window(
    duration_hours: float,
    start_day: int,
    start_hour: int,
    offset: int = 0,
) -> tuple[datetime, datetime]:
    """Return (cycle_start, cycle_end) for the current or offset cycle."""
    now = datetime.now(timezone.utc)

    # Duration in calendar days (assuming 8h/day)
    duration_days = duration_hours / 8

    # Find the most recent cycle start
    # Walk backward from now to find the last occurrence of start_day at start_hour
    candidate = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    # Adjust to the correct weekday
    days_since = (candidate.weekday() - start_day) % 7
    candidate -= timedelta(days=days_since)
    if candidate > now:
        candidate -= timedelta(weeks=1)

    # Apply offset (negative = previous cycles)
    cycle_start = candidate + timedelta(days=duration_days * offset)
    cycle_end = cycle_start + timedelta(days=duration_days)
    return cycle_start, cycle_end


def _setup_cycle_config_interactive(target_name: str, targets: dict) -> dict:
    """Interactively configure cycle settings for a target."""
    target = targets.get(target_name, {})
    cycle = target.get("cycle", {})

    print(f"\nCycle configuration for target '{target_name}':")
    print(f"  Current duration: {cycle.get('duration', 'not set')}")
    print(f"  Current start day: {cycle.get('start_day', 'not set')}")
    print(f"  Current start hour: {cycle.get('start_hour', 'not set')}")
    print()

    duration = input(f"  Cycle duration in Jira notation (e.g. 1w, 4d, 8h) [{cycle.get('duration', '1w')}]: ").strip()
    if not duration:
        duration = cycle.get("duration", "1w")
    # Validate
    try:
        _parse_jira_duration(duration)
    except ValueError:
        print(f"  Invalid duration '{duration}', using '1w'")
        duration = "1w"

    day = input(f"  Cycle start day (e.g. Monday) [{cycle.get('start_day', 'Monday')}]: ").strip()
    if not day:
        day = cycle.get("start_day", "Monday")

    hour = input(f"  Cycle start hour (0-23) [{cycle.get('start_hour', 9)}]: ").strip()
    if not hour:
        hour = str(cycle.get("start_hour", 9))
    try:
        hour_int = int(hour)
        if not 0 <= hour_int <= 23:
            raise ValueError
    except ValueError:
        print(f"  Invalid hour '{hour}', using 9")
        hour_int = 9

    cycle_config = {"duration": duration, "start_day": day, "start_hour": hour_int}
    target["cycle"] = cycle_config
    targets[target_name] = target
    save_targets(targets)
    print(f"  ✓ Saved cycle config: {duration}, starts {day} at {hour_int}:00")
    return cycle_config


def _run_report(args: argparse.Namespace) -> None:
    """Generate a cycle report for a target."""
    _, target_name, _, handler = resolve_runtime(args)
    targets = load_targets()
    target = targets.get(target_name, {})
    cycle_config = target.get("cycle")

    if not cycle_config:
        print(f"No cycle configured for target '{target_name}'.")
        reply = input("Set up cycle config now? [Y/n] ").strip().lower()
        if reply in ("", "y", "yes"):
            cycle_config = _setup_cycle_config_interactive(target_name, targets)
        else:
            raise SystemExit("Cannot generate report without cycle configuration.")

    # Parse cycle parameters
    duration_hours = _parse_jira_duration(cycle_config["duration"])
    start_day = _parse_day_name(cycle_config.get("start_day", "Monday"))
    start_hour = int(cycle_config.get("start_hour", 9))
    offset = getattr(args, "previous", 0) or 0

    cycle_start, cycle_end = _compute_cycle_window(duration_hours, start_day, start_hour, offset)

    print(f"\n{'=' * 60}")
    print(f"  Report for target: {target_name}")
    print(f"  Cycle: {cycle_start.strftime('%a %b %-d %H:%M')} → {cycle_end.strftime('%a %b %-d %H:%M')}")
    if offset:
        print(f"  (offset: {offset})")
    print(f"{'=' * 60}\n")

    # Fetch all unresolved issues (no cache — direct from backend)
    all_issues = handler.list_issues(all_unresolved=True)

    # For each issue, fetch details with changelog (no cache)
    detailed: list = []
    for item in all_issues:
        try:
            if hasattr(handler, "get_issue_details_with_changelog"):
                issue = handler.get_issue_details_with_changelog(item.key)
            else:
                issue = handler.get_issue_details(item.key)
                try:
                    issue.changelog = handler.get_issue_changelog(item.key)
                except Exception:
                    pass
            detailed.append(issue)
        except Exception as exc:
            print(f"  Warning: could not fetch {item.key}: {exc}")

    def _in_window(ts: str | None) -> bool:
        if not ts:
            return False
        cleaned = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', ts.strip())
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                dt = datetime.strptime(cleaned, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return cycle_start <= dt <= cycle_end
            except (ValueError, OverflowError):
                continue
        return False

    def _parse_ts(ts: str | None) -> datetime | None:
        if not ts:
            return None
        cleaned = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', ts.strip())
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                dt = datetime.strptime(cleaned, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, OverflowError):
                continue
        return None

    # ──── Section 1: Closed tickets ────
    closed_statuses = {"done", "closed", "resolved", "complete", "completed"}
    closed_in_cycle = []
    for issue in detailed:
        for entry in issue.changelog:
            if entry.field.lower() in ("status", "state", "system.state") and _in_window(entry.created_at):
                if entry.to_value and entry.to_value.lower() in closed_statuses:
                    closed_in_cycle.append(issue)
                    break

    print("── Closed Tickets ──")
    if closed_in_cycle:
        for issue in closed_in_cycle:
            print(f"  {issue.key} | {issue.assignee or '-'} | {issue.summary}")
    else:
        print("  (none)")

    # ──── Section 2: Reassigned tickets ────
    reassigned_in_cycle = []
    for issue in detailed:
        for entry in issue.changelog:
            if entry.field.lower() in ("assignee", "system.assignedto") and _in_window(entry.created_at):
                reassigned_in_cycle.append((issue, entry))
                break

    print("\n── Reassigned Tickets ──")
    if reassigned_in_cycle:
        for issue, entry in reassigned_in_cycle:
            print(f"  {issue.key} | {entry.from_value or '?'} → {entry.to_value or '?'} | {issue.summary}")
    else:
        print("  (none)")

    # ──── Section 3: Links added ────
    links_added_in_cycle = []
    for issue in detailed:
        for entry in issue.changelog:
            if entry.field.lower() in ("link", "issuelink", "system.linkedworkitems") and _in_window(entry.created_at):
                links_added_in_cycle.append((issue, entry))

    print("\n── Links Added ──")
    if links_added_in_cycle:
        for issue, entry in links_added_in_cycle:
            print(f"  {issue.key} | {entry.to_value or '?'} | {issue.summary}")
    else:
        print("  (none)")

    # ──── Section 4: Pinned comments (progress) ────
    print("\n── Pinned Comments (Progress) ──")
    any_pinned = False
    for issue in detailed:
        pinned = [c for c in issue.comments if c.pinned]
        if pinned:
            any_pinned = True
            # Show latest pinned comment in the cycle window, or latest overall
            in_window = [c for c in pinned if _in_window(c.created_at)]
            display = in_window[-1] if in_window else pinned[-1]
            print(f"  {issue.key} | {issue.assignee or '-'} | {issue.summary}")
            print(f"    📌 [{_human_date(display.created_at)}] {display.author}: {display.body[:120]}")
    if not any_pinned:
        print("  (none)")

    # ──── Section 5: Activity (worklogs + status changes) ────
    print("\n── Activity (Worklogs & Status Changes) ──")
    any_activity = False
    for issue in detailed:
        activities = []
        for w in issue.worklogs:
            if _in_window(w.created_at):
                activities.append(f"worklog: {w.author} ({w.time_spent or '?'})")
        for entry in issue.changelog:
            if entry.field.lower() in ("status", "state", "system.state") and _in_window(entry.created_at):
                activities.append(f"status: {entry.from_value or '?'} → {entry.to_value or '?'} by {entry.author or '?'}")
        if activities:
            any_activity = True
            print(f"  {issue.key} | {issue.assignee or '-'} | {issue.summary}")
            for a in activities:
                print(f"    • {a}")
    if not any_activity:
        print("  (none)")

    # ──── Roster tables (only with --roster) ────
    if getattr(args, "roster", False):
        _print_roster(detailed, cycle_start, cycle_end, _in_window, _parse_ts)


def _parse_worklog_hours(time_spent: str | None) -> float:
    """Parse a worklog time_spent string (e.g. '2h 30m', '1d 4h') to hours."""
    if not time_spent:
        return 0.0
    total = 0.0
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*([dhm])", time_spent.lower()):
        val = float(match.group(1))
        unit = match.group(2)
        if unit == "d":
            total += val * 8
        elif unit == "h":
            total += val
        elif unit == "m":
            total += val / 60
    return total


def _print_roster(detailed, cycle_start, cycle_end, _in_window, _parse_ts) -> None:
    """Print two roster tables: ticket-centered and person-centered."""

    # Collect worklog data
    # ticket_worklogs: {issue_key: {person: hours}}
    ticket_worklogs: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    ticket_summaries: dict[str, str] = {}
    person_totals: dict[str, float] = defaultdict(float)

    for issue in detailed:
        for w in issue.worklogs:
            if _in_window(w.created_at):
                hours = _parse_worklog_hours(w.time_spent)
                ticket_worklogs[issue.key][w.author] += hours
                ticket_summaries[issue.key] = issue.summary
                person_totals[w.author] += hours

    print(f"\n{'─' * 60}")
    print("  Roster: Worklogs by Ticket")
    print(f"{'─' * 60}")

    if ticket_worklogs:
        for key in sorted(ticket_worklogs.keys()):
            summary = ticket_summaries.get(key, "")[:50]
            print(f"\n  {key} — {summary}")
            people = ticket_worklogs[key]
            total = 0.0
            for person in sorted(people.keys()):
                hours = people[person]
                total += hours
                print(f"    {person:30s}  {_format_hours(hours)}")
            print(f"    {'TOTAL':30s}  {_format_hours(total)}")
    else:
        print("  (no worklogs in this cycle)")

    print(f"\n{'─' * 60}")
    print("  Roster: Total Hours by Person")
    print(f"{'─' * 60}")

    if person_totals:
        grand_total = 0.0
        for person in sorted(person_totals.keys()):
            hours = person_totals[person]
            grand_total += hours
            print(f"  {person:32s}  {_format_hours(hours)}")
        print(f"  {'GRAND TOTAL':32s}  {_format_hours(grand_total)}")
    else:
        print("  (no worklogs in this cycle)")

    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ticketcli", description="Multi-backend ticket CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("add", help="Create an issue")
    _add_target_argument(p)
    p.add_argument("-s", "--summary", help="Issue summary")
    p.add_argument("-d", "--description", help="Issue description")
    assignee_arg = p.add_argument("-a", "--assignee", help="Human or system assignee name")
    assignee_arg.completer = assignee_completer
    labels_arg = p.add_argument("-l", "--labels", nargs="*", help="Labels to apply (space-separated)")
    labels_arg.completer = label_completer
    comp_arg = p.add_argument("--components", nargs="*", help="Components to set (space-separated)")
    comp_arg.completer = component_completer
    p.add_argument("--edit", action="store_true", help="Open description in configured editor")
    p.set_defaults(func=_run_add)

    p = subparsers.add_parser("edit", help="Edit an issue")
    _add_target_argument(p)
    _add_issue_argument(p, required=True)
    p.add_argument("-s", "--summary", help="New summary")
    p.add_argument("-d", "--description", help="New description")
    _add_assignee_argument(p)
    labels_arg = p.add_argument("-l", "--labels", nargs="*", help="Labels to set (space-separated)")
    labels_arg.completer = label_completer
    comp_arg = p.add_argument("--components", nargs="*", help="Components to set (space-separated)")
    comp_arg.completer = component_completer
    p.add_argument("--unassign", action="store_true", help="Clear the assignee")
    p.add_argument("--edit", action="store_true", help="Open description in editor")
    p.add_argument("--pin", action="store_true", help="Interactively pin/unpin comments")
    p.set_defaults(func=_run_edit, assignee_interactive=False, unassign=False)

    p = subparsers.add_parser("assign", help="Assign, reassign, or unassign an issue")
    _add_target_argument(p)
    _add_issue_argument(p, required=True)
    _add_assignee_argument(p)
    p.add_argument("--unassign", action="store_true", help="Clear the assignee")
    p.set_defaults(func=_run_assign, assignee_interactive=False, unassign=False)

    p = subparsers.add_parser("comment", help="Add a comment to an issue")
    _add_target_argument(p)
    _add_issue_argument(p, required=True)
    p.add_argument("-m", "--message", help="Comment text")
    p.add_argument("--author", help="Human or system author name")
    p.set_defaults(func=_run_comment)

    p = subparsers.add_parser("show", help="Show issue details")
    _add_target_argument(p)
    _add_issue_argument(p, required=True)
    p.add_argument("-f", "--format", choices=("plain", "json"), default="plain", help="Output format")
    p.set_defaults(func=_run_show)

    p = subparsers.add_parser("attachments", help="Download selected attachments")
    _add_target_argument(p)
    _add_issue_argument(p, required=True)
    p.add_argument("-o", "--output-dir", help="Output directory", default=".")
    p.set_defaults(func=_run_attachments)

    p = subparsers.add_parser("list", help="List issues")
    _add_target_argument(p)
    p.add_argument("-c", "--created", action="store_true", help="Show tickets created by me instead of unresolved tickets assigned to me")
    p.add_argument("-a", "--all", action="store_true", help="Show all unresolved tickets in the target")
    p.add_argument("-f", "--format", choices=("plain", "json"), default="plain", help="Output format")
    p.set_defaults(func=_run_list)

    p = subparsers.add_parser("target", help="Set or clear default target behavior")
    group = p.add_mutually_exclusive_group()
    default_arg = group.add_argument("--default", dest="default_target", help="Set default target")
    default_arg.completer = target_completer
    group.add_argument("--clear-default", action="store_true", help="Clear default target")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--require-target", action="store_true", help="Always require --target")
    mode.add_argument("--allow-default", action="store_true", help="Allow using the default target when --target is omitted")
    p.set_defaults(func=_run_target)

    p = subparsers.add_parser("targets", help="List configured targets")
    p.set_defaults(func=_run_targets)

    p = subparsers.add_parser("migrate", help="Migrate an issue from one target to another")
    source_arg = p.add_argument("--source", required=True, help="Source target name")
    source_arg.completer = target_completer
    dest_arg = p.add_argument("--dest", required=True, help="Destination target name")
    dest_arg.completer = target_completer
    _add_issue_argument(p, required=False)
    p.add_argument("--comments", action="store_true", help="Also migrate comments")
    p.add_argument("--attachments", action="store_true", help="Also migrate attachments")
    p.add_argument("--no-assignee", action="store_true", help="Do not carry over the assignee")
    p.add_argument("--close-source", action="store_true", help="Attempt to close the source issue after migration")
    p.add_argument("--dry-run", action="store_true", help="Show what would be migrated without making changes")
    p.add_argument("-a", "--all", action="store_true", help="Migrate all unresolved issues from the source")
    p.set_defaults(func=_run_migrate)

    p = subparsers.add_parser("upload", help="Upload an attachment to an issue")
    _add_target_argument(p)
    _add_issue_argument(p, required=True)
    p.add_argument("file", help="Path to the file to upload")
    p.set_defaults(func=_run_upload)

    p = subparsers.add_parser("delete-attachment", help="Delete an attachment from an issue")
    _add_target_argument(p)
    _add_issue_argument(p, required=True)
    p.add_argument("attachment_id", help="Attachment ID (shown in 'ticketcli show')")
    p.set_defaults(func=_run_delete_attachment)

    p = subparsers.add_parser("status", help="Change the status of an issue")
    _add_target_argument(p)
    _add_issue_argument(p, required=True)
    p.add_argument("status", help="Target status name (e.g. 'In Progress', 'Done')")
    p.set_defaults(func=_run_status)

    p = subparsers.add_parser("close", help="Close an issue (shortcut for status Done)")
    _add_target_argument(p)
    _add_issue_argument(p, required=True)
    p.add_argument("-s", "--status", default=None, help="Override the close status (default: Done)")
    p.set_defaults(func=_run_close)

    p = subparsers.add_parser("cache", help="Manage completion cache")
    p.add_argument("cache_action", choices=("clear",), help="Cache action to perform")
    p.set_defaults(func=_run_cache)

    p = subparsers.add_parser("show-report", help="Generate a cycle report")
    _add_target_argument(p)
    p.add_argument("-p", "--previous", type=int, default=0, help="Offset to previous cycle (e.g. -1 for last cycle)")
    p.add_argument("-r", "--roster", action="store_true", help="Include roster tables (worklogs by ticket and person)")
    p.set_defaults(func=_run_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argcomplete.autocomplete(parser, always_complete_options=False)
    args = parser.parse_args(argv)

    if getattr(args, "created", False) and getattr(args, "all", False) and args.command == "list":
        raise SystemExit("Cannot use --created and --all together.")

    if args.command == "migrate":
        has_issue = bool(getattr(args, "issue", None))
        has_all = bool(getattr(args, "all", False))
        if not has_issue and not has_all:
            raise SystemExit("migrate requires either --issue/-i or --all.")
        if has_issue and has_all:
            raise SystemExit("Cannot use --issue and --all together for migrate.")

    if getattr(args, "unassign", False) and getattr(args, "assignee", None) not in (None, ""):
        raise SystemExit("Cannot use --assignee and --unassign together.")

    if getattr(args, "assignee", None) == "":
        args.assignee = None
        args.assignee_interactive = True

    try:
        func: HandlerFn = args.func
        func(args)
        return 0
    except ConfigError as exc:
        raise SystemExit(f"Config error: {exc}") from exc
    except NotImplementedError as exc:
        raise SystemExit(str(exc)) from exc
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    raise SystemExit(main())