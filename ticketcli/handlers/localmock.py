from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ticketcli.handlers.base import TicketHandler
from ticketcli.models import Attachment, ChangelogEntry, Comment, Issue
from ticketcli.utils.me import resolve_me


class LocalMockHandler(TicketHandler):
    """Useful for local testing before wiring real APIs."""

    def __init__(self, target_name: str, target_config: dict[str, Any], user_mappings: dict[str, Any]):
        super().__init__(target_name, target_config, user_mappings)
        self.db_path = Path(target_config.get("mock_db", Path.home() / ".ticketcli" / f"{target_name}_issues.json"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self._save([])

    def _load_issues(self) -> list[Issue]:
        if not self.db_path.exists():
            return []

        raw = self.db_path.read_text(encoding="utf-8").strip()
        if not raw:
            return []

        data = json.loads(raw)

        if isinstance(data, list):
            return [Issue.from_dict(x) for x in data if isinstance(x, dict)]

        if isinstance(data, dict):
            return [Issue.from_dict(x) for x in data.get("issues", []) if isinstance(x, dict)]

        return []

    def _save(self, issues: list[Issue]) -> None:
        payload = [issue.to_dict() for issue in issues]
        self.db_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _next_key(self) -> str:
        project_base = self.target_config.get("project_base", self.target_name.upper())
        issues = self._load_issues()
        next_num = len(issues) + 1
        return f"{project_base}-{next_num}"

    def _from_raw(self, raw: dict[str, Any]) -> Issue:
        return Issue.from_dict(raw)

    def create_issue(self, summary: str, description: str = "", assignee: str | None = None, **kwargs: Any) -> Issue:
        issues = self._load_issues()
        me = resolve_me(self.target_name, self.target_config)

        issue_number = len(issues) + 1
        key = f"{self.target_config.get('project_base', 'MOCK')}-{issue_number}"

        labels = list(kwargs.get("labels") or [])
        components = list(kwargs.get("components") or [])
        assignees = list(kwargs.get("assignees") or [])
        assignee_final = assignee or (assignees[0] if assignees else me)

        issue = Issue(
            key=key,
            summary=summary,
            description=description,
            status="Open",
            assignee=assignee_final,
            assignees=assignees,
            creator=me,
            labels=labels,
            components=components,
            attachments=[],
            comments=[],
            worklogs=[],
        )

        issues.append(issue)
        self._save(issues)
        return issue

    def edit_issue(self, issue_key: str, summary: str | None = None, description: str | None = None, **kwargs: Any) -> Issue:
        key = self.normalize_issue_key(issue_key)
        issues = self._load_issues()
        for issue in issues:
            if issue.key == key:
                if summary is not None:
                    issue.summary = summary
                if description is not None:
                    issue.description = description
                if "assignee" in kwargs:
                    issue.assignee = kwargs["assignee"]
                self._save(issues)
                return self._from_raw(issue.to_dict())
        raise KeyError(f"Issue not found: {key}")

    def add_comment(self, issue_key: str, comment: str, **kwargs: Any) -> None:
        key = self.normalize_issue_key(issue_key)
        issues = self._load_issues()
        for issue in issues:
            if issue.key == key:
                issue.comments.append(
                    Comment(
                        id=str(uuid4()),
                        author=resolve_me(self.target_name, self.target_config) or "unknown",
                        body=comment,
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
                self._save(issues)
                return
        raise KeyError(f"Issue not found: {key}")

    def get_issue_details(self, issue_key: str) -> Issue:
        key = self.normalize_issue_key(issue_key)
        issues = self._load_issues()
        for issue in issues:
            if issue.key == key:
                return issue
        raise KeyError(f"Issue not found: {key}")

    def download_attachments(self, issue_key: str, attachment_ids: list[str], output_dir: Path) -> list[Path]:
        issue = self.get_issue_details(issue_key)
        output_dir.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        attachments = {a.id: a for a in issue.attachments}
        for attachment_id in attachment_ids:
            attachment = attachments.get(attachment_id)
            if not attachment:
                continue
            target = output_dir / attachment.name
            # Serve the real stored file if the download_url points to a local path
            source = Path(attachment.download_url) if attachment.download_url else None
            if source and source.is_file():
                import shutil
                shutil.copy2(source, target)
            else:
                target.write_text(
                    f"[localmock] Original file not found for attachment {attachment.name} "
                    f"(expected at {attachment.download_url}).\n",
                    encoding="utf-8",
                )
            saved.append(target)
        return saved

    def list_issues(self, created_by_me: bool = False, all_unresolved: bool = False):
        me = resolve_me(self.target_name, self.target_config)
        issues = self._load_issues()

        results = []

        for issue in issues:
            if issue.status and issue.status.lower() in {"done", "closed", "resolved"}:
                continue

            if created_by_me:
                if issue.creator != me:
                    continue
            elif not all_unresolved:
                if issue.assignee != me:
                    continue

            results.append(issue)

        return results

    def upload_attachment(self, issue_key: str, file_path: Path) -> None:
        key = self.normalize_issue_key(issue_key)
        issues = self._load_issues()
        for issue in issues:
            if issue.key == key:
                # Store file content in a local attachments dir
                att_dir = self.db_path.parent / f"{self.target_name}_attachments" / key
                att_dir.mkdir(parents=True, exist_ok=True)
                dest = att_dir / file_path.name
                dest.write_bytes(file_path.read_bytes())
                att_id = str(uuid4())
                issue.attachments.append(
                    Attachment(
                        id=att_id,
                        name=file_path.name,
                        download_url=str(dest),
                        size=file_path.stat().st_size,
                    )
                )
                self._save(issues)
                return
        raise KeyError(f"Issue not found: {key}")

    def delete_attachment(self, issue_key: str, attachment_id: str) -> None:
        key = self.normalize_issue_key(issue_key)
        issues = self._load_issues()
        for issue in issues:
            if issue.key == key:
                original_len = len(issue.attachments)
                issue.attachments = [a for a in issue.attachments if a.id != attachment_id]
                if len(issue.attachments) == original_len:
                    raise KeyError(f"Attachment not found: {attachment_id}")
                self._save(issues)
                return
        raise KeyError(f"Issue not found: {key}")

    def list_transitions(self, issue_key: str) -> list[str]:
        """Return the fixed set of statuses available in the local mock."""
        return ["Open", "In Progress", "Done", "Closed"]

    def transition_issue(self, issue_key: str, target_status: str) -> None:
        key = self.normalize_issue_key(issue_key)
        issues = self._load_issues()
        for issue in issues:
            if issue.key == key:
                issue.status = target_status
                self._save(issues)
                return
        raise KeyError(f"Issue not found: {key}")

    def pin_comment(self, issue_key: str, comment_id: str) -> None:
        key = self.normalize_issue_key(issue_key)
        issues = self._load_issues()
        for issue in issues:
            if issue.key == key:
                for comment in issue.comments:
                    if comment.id == comment_id:
                        comment.pinned = True
                        self._save(issues)
                        return
                raise KeyError(f"Comment not found: {comment_id}")
        raise KeyError(f"Issue not found: {key}")

    def unpin_comment(self, issue_key: str, comment_id: str) -> None:
        key = self.normalize_issue_key(issue_key)
        issues = self._load_issues()
        for issue in issues:
            if issue.key == key:
                for comment in issue.comments:
                    if comment.id == comment_id:
                        comment.pinned = False
                        self._save(issues)
                        return
                raise KeyError(f"Comment not found: {comment_id}")
        raise KeyError(f"Issue not found: {key}")

    def get_issue_changelog(self, issue_key: str) -> list[ChangelogEntry]:
        """Local mock has no changelog tracking — return empty."""
        return []