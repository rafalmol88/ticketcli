from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from ticketcli.handlers.base import TicketHandler
from ticketcli.models import Attachment, ChangelogEntry, Comment, Issue, IssueLink, IssueListItem, Worklog


class JiraBaseHandler(TicketHandler):
    api_version = "3"
    assignee_payload_field = "accountId"

    def __init__(self, target_name: str, target_config: dict[str, Any], user_mappings: dict[str, Any]):
        super().__init__(target_name, target_config, user_mappings)
        self.base_url = str(target_config.get("base_url", "")).rstrip("/")
        if not self.base_url:
            raise ValueError(f"Target '{target_name}' is missing required key: base_url")
        self.timeout = int(target_config.get("timeout_seconds", 60))
        self.session = self._build_session(target_config)
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    def _build_session(self, target_config: dict[str, Any]) -> requests.Session:
        raise NotImplementedError

    def _api_path(self, suffix: str) -> str:
        return f"/rest/api/{self.api_version}/{suffix.lstrip('/')}"

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.base_url}{path}"
        response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        if response.status_code >= 400:
            detail = response.text.strip()
            raise RuntimeError(f"Jira API {method} {path} failed with {response.status_code}: {detail}")
        return response

    @staticmethod
    def _adf_text(text: str) -> dict[str, Any]:
        paragraphs = []
        blocks = text.split("\n\n") if text else [""]
        for block in blocks:
            lines = block.splitlines() or [""]
            content = []
            for idx, line in enumerate(lines):
                if line:
                    content.append({"type": "text", "text": line})
                if idx < len(lines) - 1:
                    content.append({"type": "hardBreak"})
            paragraphs.append({"type": "paragraph", "content": content or [{"type": "text", "text": ""}]})
        return {"type": "doc", "version": 1, "content": paragraphs}

    @classmethod
    def _adf_to_text(cls, node: Any) -> str:
        if node is None:
            return ""
        if isinstance(node, str):
            return node
        if isinstance(node, list):
            return "".join(cls._adf_to_text(item) for item in node)
        if not isinstance(node, dict):
            return str(node)

        node_type = node.get("type")
        if node_type == "text":
            return node.get("text", "")
        if node_type == "hardBreak":
            return "\n"

        children = node.get("content", [])
        text = "".join(cls._adf_to_text(child) for child in children)
        if node_type in {"paragraph", "heading"}:
            return text + "\n"
        if node_type == "listItem":
            return f"- {text.strip()}\n"
        if node_type == "codeBlock":
            return f"```\n{text.rstrip()}\n```\n"
        return text

    def _format_description_for_write(self, description: str) -> Any:
        return self._adf_text(description)

    def _parse_description_for_read(self, description: Any) -> str:
        return self._adf_to_text(description).strip()

    def _format_comment_for_write(self, comment: str) -> Any:
        return self._adf_text(comment)

    def _parse_comment_for_read(self, body: Any) -> str:
        return self._adf_to_text(body).strip()

    def _make_assignee_value(self, assignee: str | None) -> Any:
        if assignee is None:
            return None
        if assignee == "":
            return None
        return {self.assignee_payload_field: assignee}

    def _issue_fields_payload(self, summary: str | None = None, description: str | None = None, assignee: str | None = None, labels: list[str] | None = None, components: list[str] | None = None) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if summary is not None:
            fields["summary"] = summary
        if description is not None:
            fields["description"] = self._format_description_for_write(description)
        if assignee is not None:
            fields["assignee"] = self._make_assignee_value(assignee)
        if labels is not None:
            fields["labels"] = labels
        if components is not None:
            fields["components"] = [{"name": c} for c in components]
        return fields

    def _parse_issue(self, raw: dict[str, Any]) -> Issue:
        fields = raw.get("fields", {})
        attachments = [
            Attachment(
                id=str(item.get("id", "")),
                name=item.get("filename") or item.get("name") or str(item.get("id", "attachment")),
                download_url=item.get("content") or item.get("self"),
                size=item.get("size"),
            )
            for item in fields.get("attachment", [])
        ]

        comments_block = fields.get("comment", {}) or {}
        comment_items = comments_block.get("comments", comments_block if isinstance(comments_block, list) else [])
        comments = []
        for item in comment_items:
            # Detect pinned: Jira Cloud uses comment properties (key=sd.public.pinned)
            # or the "properties" list on the comment object.
            pinned = False
            props = item.get("properties") or []
            for prop in props:
                if isinstance(prop, dict) and prop.get("key") in ("sd.public.pinned", "pinned"):
                    pinned = True
                    break
            # Fallback: check renderedBody or a custom "pinned" field
            if not pinned and item.get("pinned"):
                pinned = True
            comments.append(
                Comment(
                    id=str(item.get("id", "")),
                    author=(item.get("author") or {}).get("displayName") or (item.get("author") or {}).get("accountId") or (item.get("author") or {}).get("name") or "unknown",
                    body=self._parse_comment_for_read(item.get("body")),
                    created_at=item.get("created") or item.get("updated"),
                    pinned=pinned,
                )
            )

        worklog_block = fields.get("worklog", {}) or {}
        worklog_items = worklog_block.get("worklogs", worklog_block if isinstance(worklog_block, list) else [])
        worklogs = [
            Worklog(
                id=str(item.get("id", "")),
                author=(item.get("author") or {}).get("displayName") or (item.get("author") or {}).get("accountId") or (item.get("author") or {}).get("name") or "unknown",
                body=self._parse_comment_for_read(item.get("comment")),
                time_spent=item.get("timeSpent"),
                created_at=item.get("created") or item.get("updated") or item.get("started"),
            )
            for item in worklog_items
        ]

        assignee_obj = fields.get("assignee") or {}

        raw_labels = fields.get("labels") or []
        raw_components = fields.get("components") or []
        component_names = [c.get("name", "") if isinstance(c, dict) else str(c) for c in raw_components]

        # Parse issue links
        links: list[IssueLink] = []
        for link_item in fields.get("issuelinks") or []:
            link_type_name = (link_item.get("type") or {}).get("outward") or (link_item.get("type") or {}).get("name") or ""
            # outwardIssue or inwardIssue
            linked = link_item.get("outwardIssue") or link_item.get("inwardIssue") or {}
            if not linked:
                continue
            linked_key = linked.get("key", "")
            linked_summary = (linked.get("fields") or {}).get("summary")
            if link_item.get("inwardIssue") and not link_item.get("outwardIssue"):
                link_type_name = (link_item.get("type") or {}).get("inward") or link_type_name
            links.append(IssueLink(
                link_type=link_type_name,
                outward_key=linked_key,
                outward_summary=linked_summary,
            ))

        # Parse changelog if present (expanded via ?expand=changelog)
        changelog_entries: list[ChangelogEntry] = []
        for history in (raw.get("changelog") or {}).get("histories") or []:
            author_obj = history.get("author") or {}
            author = author_obj.get("displayName") or author_obj.get("accountId") or author_obj.get("name") or "unknown"
            created = history.get("created")
            for change_item in history.get("items") or []:
                changelog_entries.append(ChangelogEntry(
                    field=change_item.get("field", ""),
                    from_value=change_item.get("fromString"),
                    to_value=change_item.get("toString"),
                    author=author,
                    created_at=created,
                ))

        return Issue(
            id=str(raw.get("id", "")),
            key=raw.get("key", ""),
            summary=fields.get("summary", ""),
            description=self._parse_description_for_read(fields.get("description")),
            status=(fields.get("status") or {}).get("name"),
            assignee=assignee_obj.get("displayName") or assignee_obj.get("accountId") or assignee_obj.get("name"),
            labels=list(raw_labels),
            components=[c for c in component_names if c],
            attachments=attachments,
            comments=comments,
            worklogs=worklogs,
            links=links,
            changelog=changelog_entries,
            raw=raw,
        )

    def create_issue(self, summary: str, description: str, **kwargs: Any) -> Issue:
        project: dict[str, str] = {}
        if self.target_config.get("project_id"):
            project["id"] = str(self.target_config["project_id"])
        elif self.target_config.get("project_key"):
            project["key"] = str(self.target_config["project_key"])
        elif self.target_config.get("project_base"):
            project["key"] = str(self.target_config["project_base"])
        else:
            raise ValueError(f"Target '{self.target_name}' must define project_id, project_key, or project_base for Jira")

        issue_type: dict[str, str] = {}
        if self.target_config.get("issue_type_id"):
            issue_type["id"] = str(self.target_config["issue_type_id"])
        else:
            issue_type["name"] = str(self.target_config.get("issue_type_name", "Task"))

        fields = {
            "project": project,
            "issuetype": issue_type,
            **self._issue_fields_payload(
                summary=summary,
                description=description,
                assignee=kwargs.get("assignee"),
                labels=kwargs.get("labels"),
                components=kwargs.get("components"),
            ),
        }
        response = self._request("POST", self._api_path("issue"), json={"fields": fields})
        payload = response.json()
        return self.get_issue_details(payload.get("key") or payload.get("id"))

    def edit_issue(self, issue_key: str, summary: str | None = None, description: str | None = None, **kwargs: Any) -> Issue:
        key = self.normalize_issue_key(issue_key)
        fields = self._issue_fields_payload(
            summary=summary,
            description=description,
            labels=kwargs.get("labels"),
            components=kwargs.get("components"),
        )
        if "assignee" in kwargs and kwargs["assignee"] is not None:
            fields["assignee"] = self._make_assignee_value(kwargs["assignee"])

        if not fields:
            return self.get_issue_details(key)

        self._request("PUT", self._api_path(f"issue/{key}"), json={"fields": fields})
        return self.get_issue_details(key)

    def add_comment(self, issue_key: str, comment: str, **kwargs: Any) -> None:
        key = self.normalize_issue_key(issue_key)
        self._request("POST", self._api_path(f"issue/{key}/comment"), json={"body": self._format_comment_for_write(comment)})

    def get_issue_details(self, issue_key: str) -> Issue:
        key = self.normalize_issue_key(issue_key)
        response = self._request(
            "GET",
            self._api_path(f"issue/{key}"),
            params={
                "fields": "summary,description,status,assignee,attachment,comment,worklog,labels,components,issuelinks",
                "expand": "renderedFields",
            },
        )
        return self._parse_issue(response.json())

    def get_issue_details_with_changelog(self, issue_key: str) -> Issue:
        """Fetch issue with full changelog (for reports)."""
        key = self.normalize_issue_key(issue_key)
        response = self._request(
            "GET",
            self._api_path(f"issue/{key}"),
            params={
                "fields": "summary,description,status,assignee,attachment,comment,worklog,labels,components,issuelinks",
                "expand": "renderedFields,changelog",
            },
        )
        return self._parse_issue(response.json())

    def get_issue_changelog(self, issue_key: str) -> list:
        """Return changelog entries for the issue."""
        issue = self.get_issue_details_with_changelog(issue_key)
        return issue.changelog

    def pin_comment(self, issue_key: str, comment_id: str) -> None:
        # Jira Cloud: set comment property to mark as pinned
        key = self.normalize_issue_key(issue_key)
        self._request(
            "PUT",
            self._api_path(f"issue/{key}/comment/{comment_id}/properties/pinned"),
            json={"value": True},
        )

    def unpin_comment(self, issue_key: str, comment_id: str) -> None:
        key = self.normalize_issue_key(issue_key)
        try:
            self._request(
                "DELETE",
                self._api_path(f"issue/{key}/comment/{comment_id}/properties/pinned"),
            )
        except Exception:
            pass  # property may not exist

    def download_attachments(self, issue_key: str, attachment_ids: list[str], output_dir: Path) -> list[Path]:
        issue = self.get_issue_details(issue_key)
        output_dir.mkdir(parents=True, exist_ok=True)
        by_id = {attachment.id: attachment for attachment in issue.attachments}
        saved: list[Path] = []
        for attachment_id in attachment_ids:
            attachment = by_id.get(str(attachment_id))
            if not attachment:
                continue
            url = attachment.download_url or self._api_path(f"attachment/content/{attachment.id}")
            if url.startswith("/"):
                url = f"{self.base_url}{url}"
            response = self.session.get(url, timeout=self.timeout, stream=True)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Jira attachment download failed for {attachment.id} with {response.status_code}: {response.text.strip()}"
                )
            target = output_dir / attachment.name
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        handle.write(chunk)
            saved.append(target)
        return saved
    def _project_jql_clause(self) -> str:
        """Return a JQL project clause based on target config."""
        project_key = (
            self.target_config.get("project_key")
            or self.target_config.get("project_base")
        )
        if project_key:
            return f'project = "{project_key}" AND '
        return ""

    def list_issues(self, created_by_me: bool = False, all_unresolved: bool = False) -> list[IssueListItem]:
        project_clause = self._project_jql_clause()
        if created_by_me:
            jql = f'{project_clause}creator = currentUser() ORDER BY updated DESC'
        elif all_unresolved:
            jql = f'{project_clause}resolution = Unresolved ORDER BY updated DESC'
        else:
            jql = f'{project_clause}assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC'

        response = self._request(
            "GET",
            self._api_path("search"),
            params={
                "jql": jql,
                "fields": "summary,assignee,status,labels,components",
                "maxResults": int(self.target_config.get("list_issues_max_results", 100)),
            },
        )
        payload = response.json()
        issues = payload.get("issues", []) if isinstance(payload, dict) else []
        results: list[IssueListItem] = []
        for raw in issues:
            fields = raw.get("fields", {})
            assignee_obj = fields.get("assignee") or {}
            assignee = assignee_obj.get("displayName") or assignee_obj.get("accountId") or assignee_obj.get("name")
            raw_components = fields.get("components") or []
            results.append(
                IssueListItem(
                    key=raw.get("key", ""),
                    summary=fields.get("summary", ""),
                    assignee=assignee,
                    status=(fields.get("status") or {}).get("name"),
                    labels=list(fields.get("labels") or []),
                    components=[c.get("name", "") if isinstance(c, dict) else str(c) for c in raw_components],
                )
            )
        return results

    def upload_attachment(self, issue_key: str, file_path: Path) -> None:
        key = self.normalize_issue_key(issue_key)
        url = f"{self.base_url}{self._api_path(f'issue/{key}/attachments')}"
        headers = {"X-Atlassian-Token": "no-check"}
        with file_path.open("rb") as fh:
            response = self.session.post(
                url,
                files={"file": (file_path.name, fh)},
                headers=headers,
                timeout=self.timeout,
            )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Jira attachment upload failed for {key} with {response.status_code}: {response.text.strip()}"
            )

    def delete_attachment(self, issue_key: str, attachment_id: str) -> None:
        self._request("DELETE", self._api_path(f"attachment/{attachment_id}"))

    def _fetch_transitions(self, issue_key: str) -> list[dict]:
        """Fetch raw transition dicts from the Jira transitions endpoint."""
        key = self.normalize_issue_key(issue_key)
        response = self._request("GET", self._api_path(f"issue/{key}/transitions"))
        return response.json().get("transitions", [])

    def list_transitions(self, issue_key: str) -> list[str]:
        return [t.get("name", "") for t in self._fetch_transitions(issue_key) if t.get("name")]

    def transition_issue(self, issue_key: str, target_status: str) -> None:
        key = self.normalize_issue_key(issue_key)
        transitions = self._fetch_transitions(issue_key)
        matched = None
        for t in transitions:
            if t.get("name", "").lower() == target_status.lower():
                matched = t
                break
        if not matched:
            # Try partial match
            for t in transitions:
                if target_status.lower() in t.get("name", "").lower():
                    matched = t
                    break
        if not matched:
            available = ", ".join(t.get("name", "?") for t in transitions)
            raise RuntimeError(
                f"No transition to '{target_status}' for {key}. Available: {available}"
            )
        self._request(
            "POST",
            self._api_path(f"issue/{key}/transitions"),
            json={"transition": {"id": matched["id"]}},
        )

