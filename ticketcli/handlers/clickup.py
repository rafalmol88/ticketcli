from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote as urlquote

import requests

from ticketcli.handlers.base import TicketHandler
from ticketcli.models import Attachment, ChangelogEntry, Comment, Issue, IssueLink, IssueListItem, Worklog


class ClickUpHandler(TicketHandler):
    def __init__(self, target_name: str, target_config: dict[str, Any], user_mappings: dict[str, Any]):
        super().__init__(target_name, target_config, user_mappings)
        self.base_url = str(target_config.get("base_url", "https://api.clickup.com/api/v2")).rstrip("/")
        auth = target_config.get("auth", {})
        token_env = auth.get("token_env")
        token = os.getenv(token_env) if token_env else None
        if not token:
            raise ValueError(f"ClickUp token is not available. Set env var from auth.token_env for target '{target_name}'.")

        self.list_id = str(target_config.get("list_id", "")).strip()
        self.team_id = str(target_config.get("team_id") or target_config.get("workspace_id") or "").strip()
        self.custom_task_ids = bool(target_config.get("custom_task_ids", False))
        self.include_subtasks = bool(target_config.get("include_subtasks", True))
        self.timeout = int(target_config.get("timeout_seconds", 60))

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.base_url}{path}"
        response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        if response.status_code >= 400:
            detail = response.text.strip()
            raise RuntimeError(f"ClickUp API {method} {path} failed with {response.status_code}: {detail}")
        return response

    def _task_query(self) -> dict[str, Any]:
        if self.custom_task_ids:
            if not self.team_id:
                raise ValueError(
                    f"Target '{self.target_name}' uses custom_task_ids but does not define team_id/workspace_id."
                )
            return {"custom_task_ids": "true", "team_id": self.team_id}
        return {}

    def _parse_timestamp(self, raw: Any) -> str | None:
        if raw is None or raw == "":
            return None
        try:
            value = int(str(raw))
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            return str(raw)

    def _task_id_from_issue_key(self, issue_key: str) -> str:
        key = str(issue_key).strip()
        if self.custom_task_ids:
            return self.normalize_issue_key(key)
        return key.split("-", 1)[1] if "-" in key and self.target_config.get("project_base") and key.startswith(f"{self.target_config.get('project_base')}-") else key

    def _list_payload(self) -> dict[str, Any]:
        payload = {
            "include_subtasks": str(self.include_subtasks).lower(),
            "subtasks": str(self.include_subtasks).lower(),
        }
        payload.update(self._task_query())
        return payload

    def _resolve_task(self, issue_key: str) -> dict[str, Any]:
        task_id = self._task_id_from_issue_key(issue_key)
        response = self._request("GET", f"/task/{task_id}", params=self._task_query())
        return response.json()

    def _fetch_comments(self, task_id: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"/task/{task_id}/comment", params=self._task_query())
        payload = response.json()
        if isinstance(payload, list):
            return payload
        return payload.get("comments", []) or payload.get("data", []) or []

    def _fetch_worklogs(self, task_id: str) -> list[dict[str, Any]]:
        if not self.team_id:
            return []
        now = datetime.now(timezone.utc)
        start = int((now - timedelta(days=int(self.target_config.get("time_entry_lookback_days", 3650)))).timestamp() * 1000)
        end = int((now + timedelta(days=1)).timestamp() * 1000)
        response = self._request(
            "GET",
            f"/team/{self.team_id}/time_entries",
            params={
                "task_id": task_id,
                "start_date": start,
                "end_date": end,
            },
        )
        payload = response.json()
        return payload.get("data", []) or payload.get("time_entries", []) or []

    def _parse_tags(self, raw: dict[str, Any]) -> list[str]:
        """Extract tag names from a raw ClickUp task payload."""
        labels: list[str] = []
        for tag in raw.get("tags") or []:
            if isinstance(tag, dict):
                name = tag.get("name") or tag.get("tag_name") or ""
                if name:
                    labels.append(str(name))
            elif tag:
                labels.append(str(tag))
        return labels

    def _parse_issue(self, raw: dict[str, Any], comments_raw: list[dict[str, Any]] | None = None, worklogs_raw: list[dict[str, Any]] | None = None) -> Issue:
        task_id = str(raw.get("id", ""))
        project_base = self.target_config.get("project_base")
        custom_id = raw.get("custom_id")
        key = f"{project_base}-{custom_id}" if custom_id and project_base else task_id

        attachments = []
        for item in raw.get("attachments", []) or []:
            attachments.append(
                Attachment(
                    id=str(item.get("id") or item.get("attachment_id") or item.get("url") or item.get("title") or len(attachments) + 1),
                    name=item.get("title") or item.get("filename") or item.get("name") or "attachment",
                    download_url=item.get("url") or item.get("download_url"),
                    size=item.get("size"),
                )
            )

        comments = []
        for item in comments_raw or []:
            user = item.get("user") or item.get("assignee") or {}
            # Detect pinned via emoji reactions (pushpin / 📌)
            # ClickUp API returns reactions with a 'reaction' field containing
            # the uppercase hex Unicode codepoint, e.g. "1F4CC" for 📌 pushpin.
            _PUSHPIN_CODES = {"1F4CC", "1f4cc"}  # pushpin 📌
            pinned = False
            for reaction in item.get("reactions") or []:
                code = (reaction.get("reaction") or "").strip()
                if code in _PUSHPIN_CODES or code.upper() in _PUSHPIN_CODES:
                    pinned = True
                    break
            comments.append(
                Comment(
                    id=str(item.get("id", "")),
                    author=user.get("username") or user.get("email") or user.get("id") or "unknown",
                    body=item.get("comment_text") or item.get("comment") or item.get("text_content") or "",
                    created_at=self._parse_timestamp(item.get("date") or item.get("date_created")),
                    pinned=pinned,
                )
            )

        worklogs = []
        for item in worklogs_raw or []:
            user = item.get("user") or {}
            duration_ms = item.get("duration")
            time_spent = None
            if duration_ms is not None:
                try:
                    total_minutes = int(duration_ms) // 60000
                    hours, minutes = divmod(total_minutes, 60)
                    time_spent = f"{hours}h {minutes}m" if hours else f"{minutes}m"
                except (TypeError, ValueError):
                    time_spent = str(duration_ms)
            worklogs.append(
                Worklog(
                    id=str(item.get("id", "")),
                    author=user.get("username") or user.get("email") or user.get("id") or "unknown",
                    body=item.get("description") or item.get("task_location") or "",
                    time_spent=time_spent,
                    created_at=self._parse_timestamp(item.get("start") or item.get("date_created")),
                )
            )

        assignees_raw = raw.get("assignees") or []
        assignees: list[str] = []
        for a in assignees_raw:
            a = a or {}
            uid = a.get("username") or a.get("email") or str(a.get("id") or "")
            if uid:
                assignees.append(uid)
        assignee = assignees[0] if assignees else None

        labels = self._parse_tags(raw)

        # Parse task dependencies / linked tasks
        links: list[IssueLink] = []
        for dep in raw.get("dependencies") or []:
            dep_id = str(dep.get("task_id") or dep.get("depends_on") or "")
            if dep_id:
                links.append(IssueLink(
                    link_type=dep.get("type") or "dependency",
                    outward_key=dep_id,
                    outward_summary=dep.get("task_name"),
                ))
        for linked in raw.get("linked_tasks") or []:
            linked_id = str(linked.get("task_id") or "")
            if linked_id:
                links.append(IssueLink(
                    link_type=linked.get("link_type") or "linked",
                    outward_key=linked_id,
                    outward_summary=linked.get("task_name"),
                ))

        return Issue(
            key=key,
            summary=raw.get("name", ""),
            description=raw.get("markdown_description") or raw.get("description") or raw.get("text_content") or "",
            status=(raw.get("status") or {}).get("status") or (raw.get("status") or {}).get("type"),
            assignee=assignee,
            assignees=assignees,
            labels=labels,
            attachments=attachments,
            comments=comments,
            worklogs=worklogs,
            links=links,
            raw=raw,
        )

    def create_issue(self, summary: str, description: str, **kwargs: Any) -> Issue:
        if not self.list_id:
            raise ValueError(f"Target '{self.target_name}' is missing required key: list_id")
        payload: dict[str, Any] = {
            "name": summary,
            "markdown_description": description,
        }

        # Assignees: prefer explicit list, fall back to single assignee kwarg
        desired_assignees: list[str] = [str(a) for a in (kwargs.get("assignees") or [])]
        if not desired_assignees and kwargs.get("assignee"):
            desired_assignees = [str(kwargs["assignee"])]
        if desired_assignees:
            payload["assignees"] = desired_assignees

        # Labels: send as ClickUp tag objects
        desired_labels: list[str] = [str(l) for l in (kwargs.get("labels") or [])]
        if desired_labels:
            payload["tags"] = [{"name": l} for l in desired_labels]

        response = self._request("POST", f"/list/{self.list_id}/task", json=payload)
        return self._parse_issue(response.json())

    def edit_issue(self, issue_key: str, summary: str | None = None, description: str | None = None, **kwargs: Any) -> Issue:
        task_id = self._task_id_from_issue_key(issue_key)
        payload: dict[str, Any] = {}
        if summary is not None:
            payload["name"] = summary
        if description is not None:
            payload["markdown_description"] = description

        # --- Assignees (replace semantics via add/rem diff) ---
        has_assignees_kwarg = "assignees" in kwargs or "assignee" in kwargs
        if has_assignees_kwarg:
            desired_assignees: list[str] = [str(a) for a in (kwargs.get("assignees") or [])]
            if not desired_assignees and kwargs.get("assignee") is not None:
                val = kwargs["assignee"]
                if val:  # None/empty means unassign
                    desired_assignees = [str(val)]

            # Fetch current task to compute diff
            current_raw = self._resolve_task(issue_key)
            current_ids = {str(a.get("id") or "") for a in (current_raw.get("assignees") or []) if a}
            desired_set = set(desired_assignees)
            add_ids = list(desired_set - current_ids)
            rem_ids = list(current_ids - desired_set)
            if add_ids or rem_ids:
                asgn: dict[str, Any] = {}
                if add_ids:
                    asgn["add"] = add_ids
                if rem_ids:
                    asgn["rem"] = rem_ids
                payload["assignees"] = asgn
        else:
            current_raw = None  # will be fetched lazily for labels below

        # --- Labels (add/remove diffs via per-tag endpoints) ---
        # Resolve desired labels independently of assignees branch
        desired_labels: list[str] | None = None
        if "labels" in kwargs:
            desired_labels = [str(l) for l in (kwargs["labels"] or [])]

        if payload:
            self._request("PUT", f"/task/{task_id}", params=self._task_query(), json=payload)

        if desired_labels is not None:
            # Fetch current task raw if not already fetched
            if not has_assignees_kwarg:
                current_raw = self._resolve_task(issue_key)
            current_labels = set(self._parse_tags(current_raw))
            desired_set_lbl = set(desired_labels)
            to_add = desired_set_lbl - current_labels
            to_remove = current_labels - desired_set_lbl
            for tag_name in to_add:
                self._request(
                    "POST",
                    f"/task/{task_id}/tag/{urlquote(tag_name, safe='')}",
                    params=self._task_query(),
                )
            for tag_name in to_remove:
                self._request(
                    "DELETE",
                    f"/task/{task_id}/tag/{urlquote(tag_name, safe='')}",
                    params=self._task_query(),
                )

        return self.get_issue_details(issue_key)

    def add_comment(self, issue_key: str, comment: str, **kwargs: Any) -> None:
        task_id = self._task_id_from_issue_key(issue_key)
        payload = {
            "comment_text": comment,
            "notify_all": kwargs.get("notify_all", False),
        }
        self._request("POST", f"/task/{task_id}/comment", params=self._task_query(), json=payload)

    def get_issue_details(self, issue_key: str) -> Issue:
        raw = self._resolve_task(issue_key)
        task_id = str(raw.get("id") or self._task_id_from_issue_key(issue_key))
        comments = self._fetch_comments(task_id)
        worklogs = self._fetch_worklogs(task_id)
        return self._parse_issue(raw, comments_raw=comments, worklogs_raw=worklogs)

    def download_attachments(self, issue_key: str, attachment_ids: list[str], output_dir: Path) -> list[Path]:
        issue = self.get_issue_details(issue_key)
        output_dir.mkdir(parents=True, exist_ok=True)
        by_id = {attachment.id: attachment for attachment in issue.attachments}
        saved: list[Path] = []
        for attachment_id in attachment_ids:
            attachment = by_id.get(str(attachment_id))
            if not attachment or not attachment.download_url:
                continue
            response = self.session.get(attachment.download_url, timeout=self.timeout, stream=True)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"ClickUp attachment download failed for {attachment.id} with {response.status_code}: {response.text.strip()}"
                )
            target = output_dir / attachment.name
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        handle.write(chunk)
            saved.append(target)
        return saved


    def _fetch_current_user(self) -> dict[str, Any]:
        response = self._request("GET", "/user")
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get("user"), dict):
            return payload["user"]
        return payload if isinstance(payload, dict) else {}

    def list_issues(self, created_by_me: bool = False, all_unresolved: bool = False) -> list[IssueListItem]:
        if not self.list_id:
            raise ValueError(f"Target '{self.target_name}' is missing required key: list_id")

        # Resolve filter_labels from target config (case-insensitive matching)
        filter_labels_cfg: list[str] = [str(l) for l in (self.target_config.get("filter_labels") or [])]
        filter_labels_set: set[str] = {l.lower() for l in filter_labels_cfg}

        current_user_id = ""
        if not all_unresolved:
            current_user = self._fetch_current_user()
            current_user_id = str(current_user.get("id", "")).strip()
            if not current_user_id:
                raise RuntimeError("Could not determine current ClickUp user id from /user response.")

        params = self._list_payload()
        params["page"] = 0
        # Pass server-side tag filters when configured (ClickUp supports tags[] param)
        for lbl in filter_labels_cfg:
            params.setdefault("tags[]", [])
            params["tags[]"].append(lbl)
        max_pages = int(self.target_config.get("list_issues_max_pages", 10))
        results: list[IssueListItem] = []

        for page in range(max_pages):
            params["page"] = page
            response = self._request("GET", f"/list/{self.list_id}/task", params=params)
            payload = response.json()
            tasks = payload.get("tasks", []) if isinstance(payload, dict) else []
            if not tasks:
                break

            for raw in tasks:
                status_obj = raw.get("status") or {}
                status_text = (status_obj.get("status") or status_obj.get("type") or "").strip().lower()
                assignees_raw = raw.get("assignees") or []
                creator = raw.get("creator") or {}

                if status_text in {"closed", "complete", "completed", "done", "resolved"}:
                    continue

                # Parse task labels for client-side filtering (safety net) and output
                task_labels = self._parse_tags(raw)
                if filter_labels_set:
                    task_label_names = {l.lower() for l in task_labels}
                    if not filter_labels_set.intersection(task_label_names):
                        continue

                if created_by_me:
                    creator_id = str(creator.get("id", "")).strip()
                    if creator_id != current_user_id:
                        continue
                elif not all_unresolved:
                    assignee_ids = {str((a or {}).get("id", "")).strip() for a in assignees_raw}
                    if current_user_id not in assignee_ids:
                        continue

                # Build assignees list
                task_assignees: list[str] = []
                for a in assignees_raw:
                    a = a or {}
                    uid = a.get("username") or a.get("email") or str(a.get("id") or "")
                    if uid:
                        task_assignees.append(uid)
                assignee_text = task_assignees[0] if task_assignees else None

                task_id = str(raw.get("id", ""))
                project_base = self.target_config.get("project_base")
                custom_id = raw.get("custom_id")
                key = f"{project_base}-{custom_id}" if custom_id and project_base else task_id
                results.append(
                    IssueListItem(
                        key=key,
                        summary=raw.get("name", ""),
                        assignee=assignee_text,
                        assignees=task_assignees,
                        status=(raw.get("status") or {}).get("status") or (raw.get("status") or {}).get("type"),
                        labels=task_labels,
                    )
                )

            if payload.get("last_page") is True:
                break

        return results

    def upload_attachment(self, issue_key: str, file_path: Path) -> None:
        task_id = self._task_id_from_issue_key(issue_key)
        url = f"{self.base_url}/task/{task_id}/attachment"
        params = self._task_query()
        # ClickUp attachment upload requires multipart, no Content-Type in session header
        headers = {
            "Authorization": self.session.headers["Authorization"],
        }
        with file_path.open("rb") as fh:
            response = requests.post(
                url,
                files={"attachment": (file_path.name, fh)},
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"ClickUp attachment upload failed with {response.status_code}: {response.text.strip()}"
            )

    def delete_attachment(self, issue_key: str, attachment_id: str) -> None:
        # ClickUp does not scope attachment deletion to a task — just DELETE the attachment
        response = self._request("DELETE", f"/attachment/{attachment_id}")
        # 200 is success

    def list_transitions(self, issue_key: str) -> list[str]:
        """Return available statuses for the list this target is configured for."""
        if not self.list_id:
            return []
        try:
            response = self._request("GET", f"/list/{self.list_id}")
            payload = response.json()
            statuses = payload.get("statuses") or []
            return [s.get("status", "") for s in statuses if s.get("status")]
        except Exception:
            return []

    def transition_issue(self, issue_key: str, target_status: str) -> None:
        task_id = self._task_id_from_issue_key(issue_key)
        # Resolve the status name with case-insensitive / partial matching
        available = self.list_transitions(issue_key)
        resolved = target_status
        if available:
            # Exact case-insensitive match
            for s in available:
                if s.lower() == target_status.lower():
                    resolved = s
                    break
            else:
                # Partial match
                for s in available:
                    if target_status.lower() in s.lower():
                        resolved = s
                        break
                else:
                    names = ", ".join(available)
                    raise RuntimeError(
                        f"No status matching '{target_status}' for task {task_id}. Available: {names}"
                    )
        payload = {"status": resolved}
        self._request("PUT", f"/task/{task_id}", params=self._task_query(), json=payload)

    def pin_comment(self, issue_key: str, comment_id: str) -> None:
        """Pin a comment by adding a pushpin emoji reaction."""
        self._request(
            "POST",
            f"/comment/{comment_id}/reaction",
            json={"reactions": ["pushpin"]},
        )

    def unpin_comment(self, issue_key: str, comment_id: str) -> None:
        """Unpin a comment by removing the pushpin emoji reaction."""
        self._request(
            "DELETE",
            f"/comment/{comment_id}/reaction",
            json={"reactions": ["pushpin"]},
        )

    def get_issue_changelog(self, issue_key: str) -> list[ChangelogEntry]:
        """ClickUp doesn't expose a rich changelog API — return empty."""
        return []