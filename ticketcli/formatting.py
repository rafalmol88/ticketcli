from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from ticketcli.models import Issue, Comment


_SHOW_LAST_N_COMMENTS = 5


def _human_date(raw: Optional[str]) -> str:
    """Convert an ISO-ish timestamp to a human-readable form."""
    if not raw:
        return "?"
    # Normalize timezone: +0000 → +00:00 for Python's %z
    cleaned = raw.strip()
    # Handle +HHMM or -HHMM without colon at end
    cleaned = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', cleaned)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.strftime("%a, %b %-d at %H:%M")
        except (ValueError, OverflowError):
            continue
    # Fallback: return as-is
    return raw


def _visible_comments(comments: list[Comment]) -> list[Comment]:
    """Return pinned comments plus the last N non-pinned comments."""
    pinned = [c for c in comments if c.pinned]
    unpinned = [c for c in comments if not c.pinned]
    tail = unpinned[-_SHOW_LAST_N_COMMENTS:]
    # Merge pinned + tail preserving original order
    tail_ids = {id(c) for c in tail}
    pinned_ids = {id(c) for c in pinned}
    result = [c for c in comments if id(c) in pinned_ids or id(c) in tail_ids]
    return result


def render_issue(issue: Issue, target_name: str | None = None) -> str:
    # Show every assignee when the backend exposes multiple; fall back to single assignee
    _all_assignees = issue.assignees if issue.assignees else ([issue.assignee] if issue.assignee else [])
    assignee_line = f"Assignees: {', '.join(_all_assignees)}" if _all_assignees else "Assignee: -"

    lines = [
        f"Key: {issue.key}",
        f"Summary: {issue.summary}",
        f"Status: {issue.status or '-'}",
        assignee_line,
        f"Labels: {', '.join(issue.labels) if issue.labels else '-'}",
        f"Components: {', '.join(issue.components) if issue.components else '-'}",
        "",
        "Description:",
        issue.description or "-",
        "",
        "Worklogs:",
    ]

    if not issue.worklogs_available:
        lines.append("- not available (this backend does not support time tracking)")
    elif issue.worklogs:
        for w in issue.worklogs:
            lines.append(f"- [{_human_date(w.created_at)}] {w.author} ({w.time_spent or '-'})")
            lines.append(f"  {w.body}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Links:")
    if issue.links:
        for lnk in issue.links:
            summary_part = f" — {lnk.outward_summary}" if lnk.outward_summary else ""
            lines.append(f"- {lnk.link_type}: {lnk.outward_key}{summary_part}")
    else:
        lines.append("- none")

    lines.append("")
    visible = _visible_comments(issue.comments)
    hidden = len(issue.comments) - len(visible)
    header = "Comments:"
    if hidden > 0:
        header += f" (showing {len(visible)} of {len(issue.comments)}, {hidden} older hidden)"
    lines.append(header)
    if visible:
        for c in visible:
            pinned_tag = "[pinned] " if c.pinned else ""
            lines.append(f"- {pinned_tag}[{_human_date(c.created_at)}] {c.author}")
            lines.append(f"  {c.body}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Attachments:")
    if issue.attachments:
        target_flag = f" -t {target_name}" if target_name else ""
        for a in issue.attachments:
            size = f" ({a.size} bytes)" if a.size is not None else ""
            lines.append(f"- {a.id}: {a.name}{size}")
            lines.append(f"  ticketcli attachments{target_flag} -i {issue.key} -o ./downloads")
    else:
        lines.append("- none")

    return "\n".join(lines)
