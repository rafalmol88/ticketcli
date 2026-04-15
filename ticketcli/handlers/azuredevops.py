from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from ticketcli.handlers.base import TicketHandler
from ticketcli.models import Attachment, ChangelogEntry, Comment, Issue, IssueLink, IssueListItem


class AzureDevOpsHandler(TicketHandler):
    """Handler for Azure DevOps Boards (work items)."""

    _COMMENTS_API_VERSION = "7.1-preview.4"
    _WIT_API_VERSION = "7.1"

    def __init__(self, target_name: str, target_config: dict[str, Any], user_mappings: dict[str, Any]):
        super().__init__(target_name, target_config, user_mappings)

        self.organization = str(target_config.get("organization", "")).strip()
        self.project = str(target_config.get("project", "")).strip()
        self.base_url = str(target_config.get("base_url", "")).rstrip("/")

        if not self.base_url and self.organization:
            self.base_url = f"https://dev.azure.com/{self.organization}"

        if not self.base_url:
            raise ValueError(
                f"Target '{target_name}' must define 'base_url' or 'organization' for Azure DevOps."
            )
        if not self.project:
            raise ValueError(
                f"Target '{target_name}' must define 'project' for Azure DevOps."
            )

        self.work_item_type = str(target_config.get("work_item_type", "Task")).strip()
        self.timeout = int(target_config.get("timeout_seconds", 60))
        self.max_results = int(target_config.get("list_issues_max_results", 200))

        auth_config = target_config.get("auth", {})
        pat_env = auth_config.get("pat_env") if isinstance(auth_config, dict) else None
        pat = os.getenv(pat_env) if pat_env else None
        if not pat:
            raise ValueError(
                f"Azure DevOps PAT not available. Set env var from auth.pat_env for target '{target_name}'."
            )

        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth("", pat)
        self.session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _wit_url(self, suffix: str = "") -> str:
        """Project-scoped WIT API root."""
        base = f"{self.base_url}/{self.project}/_apis/wit"
        return f"{base}/{suffix.lstrip('/')}" if suffix else base

    def _org_wit_url(self, suffix: str = "") -> str:
        """Organisation-level WIT API root (used for batch work-item fetches)."""
        base = f"{self.base_url}/_apis/wit"
        return f"{base}/{suffix.lstrip('/')}" if suffix else base

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        params = dict(kwargs.pop("params", {}) or {})
        if "api-version" not in params:
            params["api-version"] = self._WIT_API_VERSION
        response = self.session.request(method, url, params=params, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Azure DevOps API {method} {url} failed with {response.status_code}: {response.text.strip()}"
            )
        return response

    def _work_item_id(self, issue_key: str) -> int:
        """Extract numeric work-item ID from 'PROJ-42' or bare '42'."""
        key = str(issue_key).strip()
        if "-" in key:
            return int(key.rsplit("-", 1)[-1])
        return int(key)

    @staticmethod
    def _text_to_html(text: str | None) -> str:
        """Convert plain text to simple HTML for Azure DevOps description field."""
        if not text:
            return ""
        import html
        paragraphs = text.split("\n\n")
        parts = []
        for para in paragraphs:
            escaped = html.escape(para).replace("\n", "<br/>")
            parts.append(f"<p>{escaped}</p>")
        return "".join(parts)

    @staticmethod
    def _html_to_text(html: str | None) -> str:
        """Lightweight HTML -> plain-text for display."""
        if not html:
            return ""
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = (
            text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
            .replace("&nbsp;", " ")
        )
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def _parse_identity(self, value: Any) -> str | None:
        """Extract a display name from an IdentityRef dict or plain string."""
        if not value:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return (
                value.get("displayName")
                or value.get("uniqueName")
                or value.get("name")
            )
        return str(value)

    def _parse_work_item(
        self,
        raw: dict[str, Any],
        comments_raw: list[dict[str, Any]] | None = None,
    ) -> Issue:
        fields = raw.get("fields", {})
        wi_id = str(raw.get("id", ""))
        project_base = self.target_config.get("project_base", "")
        key = f"{project_base}-{wi_id}" if project_base else wi_id

        attachments: list[Attachment] = []
        for rel in raw.get("relations", []) or []:
            if rel.get("rel") != "AttachedFile":
                continue
            url = rel.get("url", "")
            attrs = rel.get("attributes", {}) or {}
            uuid_match = re.search(r"/attachments/([^/?]+)", url)
            att_id = uuid_match.group(1) if uuid_match else url
            attachments.append(
                Attachment(
                    id=att_id,
                    name=attrs.get("name") or att_id,
                    download_url=url,
                )
            )

        comments: list[Comment] = []
        for item in comments_raw or []:
            author_obj = item.get("createdBy") or {}
            comments.append(
                Comment(
                    id=str(item.get("id", "")),
                    author=self._parse_identity(author_obj) or "unknown",
                    body=self._html_to_text(item.get("text") or item.get("renderedText")),
                    created_at=item.get("createdDate"),
                )
            )

        # Parse links from relations (non-attachment relations)
        links: list[IssueLink] = []
        for rel in raw.get("relations", []) or []:
            rel_type = rel.get("rel", "")
            if rel_type == "AttachedFile":
                continue
            attrs = rel.get("attributes", {}) or {}
            link_name = attrs.get("name") or rel_type
            url = rel.get("url", "")
            # Extract work item ID from URL
            wi_match = re.search(r"/workItems/(\d+)", url)
            linked_key = ""
            if wi_match:
                linked_id = wi_match.group(1)
                linked_key = f"{project_base}-{linked_id}" if project_base else linked_id
            links.append(IssueLink(
                link_type=link_name,
                outward_key=linked_key,
                outward_summary=None,  # not available without extra fetch
            ))

        return Issue(
            key=key,
            summary=fields.get("System.Title", ""),
            description=self._html_to_text(fields.get("System.Description")),
            status=str(fields["System.State"]) if fields.get("System.State") else None,
            assignee=self._parse_identity(fields.get("System.AssignedTo")),
            attachments=attachments,
            comments=comments,
            worklogs=[],
            links=links,
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    def create_issue(self, summary: str, description: str, **kwargs: Any) -> Issue:
        patch: list[dict[str, Any]] = [
            {"op": "add", "path": "/fields/System.Title", "value": summary},
        ]
        if description:
            patch.append({"op": "add", "path": "/fields/System.Description", "value": self._text_to_html(description)})
        assignee = kwargs.get("assignee")
        if assignee:
            patch.append({"op": "add", "path": "/fields/System.AssignedTo", "value": assignee})

        response = self._request(
            "POST",
            self._wit_url(f"workitems/${self.work_item_type}"),
            json=patch,
            headers={"Content-Type": "application/json-patch+json"},
        )
        return self.get_issue_details(str(response.json().get("id")))

    def edit_issue(
        self,
        issue_key: str,
        summary: str | None = None,
        description: str | None = None,
        **kwargs: Any,
    ) -> Issue:
        wi_id = self._work_item_id(issue_key)
        patch: list[dict[str, Any]] = []
        if summary is not None:
            patch.append({"op": "add", "path": "/fields/System.Title", "value": summary})
        if description is not None:
            patch.append({"op": "add", "path": "/fields/System.Description", "value": self._text_to_html(description)})
        if "assignee" in kwargs:
            patch.append(
                {"op": "add", "path": "/fields/System.AssignedTo", "value": kwargs["assignee"] or ""}
            )
        if patch:
            self._request(
                "PATCH",
                self._wit_url(f"workitems/{wi_id}"),
                json=patch,
                headers={"Content-Type": "application/json-patch+json"},
            )
        return self.get_issue_details(issue_key)

    def add_comment(self, issue_key: str, comment: str, **kwargs: Any) -> None:
        wi_id = self._work_item_id(issue_key)
        self._request(
            "POST",
            self._wit_url(f"workItems/{wi_id}/comments"),
            json={"text": comment},
            params={"api-version": self._COMMENTS_API_VERSION},
            headers={"Content-Type": "application/json"},
        )

    def get_issue_details(self, issue_key: str) -> Issue:
        wi_id = self._work_item_id(issue_key)
        response = self._request(
            "GET",
            self._wit_url(f"workitems/{wi_id}"),
            params={
                "$expand": "relations",
                "fields": "System.Title,System.Description,System.State,System.AssignedTo",
            },
        )
        raw = response.json()

        comments_raw: list[dict[str, Any]] = []
        try:
            comments_resp = self._request(
                "GET",
                self._wit_url(f"workItems/{wi_id}/comments"),
                params={"api-version": self._COMMENTS_API_VERSION, "$top": 200},
            )
            comments_raw = comments_resp.json().get("comments", [])
        except Exception:
            pass

        return self._parse_work_item(raw, comments_raw=comments_raw)

    def download_attachments(
        self, issue_key: str, attachment_ids: list[str], output_dir: Path
    ) -> list[Path]:
        issue = self.get_issue_details(issue_key)
        output_dir.mkdir(parents=True, exist_ok=True)
        by_id = {a.id: a for a in issue.attachments}
        saved: list[Path] = []
        for attachment_id in attachment_ids:
            attachment = by_id.get(str(attachment_id))
            if not attachment or not attachment.download_url:
                continue
            url = attachment.download_url
            if not url.startswith("http"):
                url = f"{self.base_url}{url}"
            response = self.session.get(
                url,
                timeout=self.timeout,
                stream=True,
                params={"api-version": self._WIT_API_VERSION, "download": "true"},
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Azure DevOps attachment download failed for {attachment.id}"
                    f" with {response.status_code}: {response.text.strip()}"
                )
            target = output_dir / attachment.name
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        handle.write(chunk)
            saved.append(target)
        return saved

    def list_issues(
        self, created_by_me: bool = False, all_unresolved: bool = False
    ) -> list[IssueListItem]:
        closed_states = "('Closed', 'Resolved', 'Done', 'Removed', 'Completed')"

        if created_by_me:
            wiql = (
                "SELECT [System.Id] FROM WorkItems"
                " WHERE [System.TeamProject] = @project"
                f" AND [System.State] NOT IN {closed_states}"
                " AND [System.CreatedBy] = @Me"
                " ORDER BY [System.ChangedDate] DESC"
            )
        elif all_unresolved:
            wiql = (
                "SELECT [System.Id] FROM WorkItems"
                " WHERE [System.TeamProject] = @project"
                f" AND [System.State] NOT IN {closed_states}"
                " ORDER BY [System.ChangedDate] DESC"
            )
        else:
            wiql = (
                "SELECT [System.Id] FROM WorkItems"
                " WHERE [System.TeamProject] = @project"
                f" AND [System.State] NOT IN {closed_states}"
                " AND [System.AssignedTo] = @Me"
                " ORDER BY [System.ChangedDate] DESC"
            )

        wiql_response = self._request(
            "POST",
            self._wit_url("wiql"),
            json={"query": wiql},
            params={"$top": self.max_results},
            headers={"Content-Type": "application/json"},
        )
        work_items = wiql_response.json().get("workItems", [])
        if not work_items:
            return []

        ids = [str(item["id"]) for item in work_items[: self.max_results]]
        batch_size = 200
        results: list[IssueListItem] = []
        project_base = self.target_config.get("project_base", "")

        for batch_start in range(0, len(ids), batch_size):
            batch_ids = ids[batch_start : batch_start + batch_size]
            details_response = self._request(
                "GET",
                self._org_wit_url("workitems"),
                params={
                    "ids": ",".join(batch_ids),
                    "fields": "System.Id,System.Title,System.State,System.AssignedTo",
                },
            )
            for raw in details_response.json().get("value", []):
                fields = raw.get("fields", {})
                wi_id = str(raw.get("id", ""))
                key = f"{project_base}-{wi_id}" if project_base else wi_id
                results.append(
                    IssueListItem(
                        key=key,
                        summary=fields.get("System.Title", ""),
                        assignee=self._parse_identity(fields.get("System.AssignedTo")),
                        status=fields.get("System.State"),
                    )
                )
        return results

    def upload_attachment(self, issue_key: str, file_path: Path) -> None:
        wi_id = self._work_item_id(issue_key)
        # Step 1: Upload the file to the attachment store
        url = f"{self.base_url}/{self.project}/_apis/wit/attachments"
        with file_path.open("rb") as fh:
            upload_resp = self._request(
                "POST",
                url,
                data=fh,
                params={"fileName": file_path.name, "uploadType": "Simple"},
                headers={"Content-Type": "application/octet-stream"},
            )
        attachment_url = upload_resp.json().get("url", "")
        if not attachment_url:
            raise RuntimeError("Azure DevOps attachment upload did not return a URL.")

        # Step 2: Link the attachment to the work item
        patch = [
            {
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "AttachedFile",
                    "url": attachment_url,
                    "attributes": {"name": file_path.name},
                },
            }
        ]
        self._request(
            "PATCH",
            self._wit_url(f"workitems/{wi_id}"),
            json=patch,
            headers={"Content-Type": "application/json-patch+json"},
        )

    def delete_attachment(self, issue_key: str, attachment_id: str) -> None:
        wi_id = self._work_item_id(issue_key)
        # Find the relation index for this attachment
        response = self._request(
            "GET",
            self._wit_url(f"workitems/{wi_id}"),
            params={"$expand": "relations"},
        )
        raw = response.json()
        relations = raw.get("relations") or []
        target_index = None
        for idx, rel in enumerate(relations):
            if rel.get("rel") != "AttachedFile":
                continue
            url = rel.get("url", "")
            if attachment_id in url:
                target_index = idx
                break
        if target_index is None:
            raise RuntimeError(f"Attachment {attachment_id} not found on work item {wi_id}.")
        patch = [{"op": "remove", "path": f"/relations/{target_index}"}]
        self._request(
            "PATCH",
            self._wit_url(f"workitems/{wi_id}"),
            json=patch,
            headers={"Content-Type": "application/json-patch+json"},
        )

    def list_transitions(self, issue_key: str) -> list[str]:
        """Return allowed states for this work item's actual type."""
        try:
            wi_id = self._work_item_id(issue_key)
            wi_resp = self._request(
                "GET",
                self._wit_url(f"workitems/{wi_id}"),
                params={"fields": "System.WorkItemType"},
            )
            work_item_type = (
                wi_resp.json().get("fields", {}).get("System.WorkItemType")
                or self.work_item_type
            )
            url = f"{self.base_url}/{self.project}/_apis/wit/workitemtypes/{work_item_type}/states"
            response = self._request("GET", url)
            states = response.json().get("value", [])
            return [s.get("name", "") for s in states if s.get("name")]
        except Exception:
            # Fallback: common Azure DevOps states
            return ["New", "Active", "Resolved", "Closed"]

    def transition_issue(self, issue_key: str, target_status: str) -> None:
        wi_id = self._work_item_id(issue_key)
        patch = [{"op": "add", "path": "/fields/System.State", "value": target_status}]
        self._request(
            "PATCH",
            self._wit_url(f"workitems/{wi_id}"),
            json=patch,
            headers={"Content-Type": "application/json-patch+json"},
        )

    def get_issue_changelog(self, issue_key: str) -> list:
        """Fetch work item updates and convert to ChangelogEntry list."""
        wi_id = self._work_item_id(issue_key)
        try:
            response = self._request(
                "GET",
                self._wit_url(f"workItems/{wi_id}/updates"),
            )
        except Exception:
            return []
        entries: list[ChangelogEntry] = []
        for update in response.json().get("value", []):
            revisedBy = update.get("revisedBy") or {}
            author = self._parse_identity(revisedBy) or "unknown"
            revised_date = update.get("revisedDate") or update.get("fields", {}).get("System.ChangedDate", {}).get("newValue")
            for field_name, change in (update.get("fields") or {}).items():
                if not isinstance(change, dict):
                    continue
                entries.append(ChangelogEntry(
                    field=field_name.replace("System.", ""),
                    from_value=str(change.get("oldValue", "")) if change.get("oldValue") is not None else None,
                    to_value=str(change.get("newValue", "")) if change.get("newValue") is not None else None,
                    author=author,
                    created_at=revised_date,
                ))
            # Relation changes
            for rel_change in (update.get("relations") or {}).get("added") or []:
                entries.append(ChangelogEntry(
                    field="Link",
                    from_value=None,
                    to_value=f"{rel_change.get('rel', '')} {rel_change.get('url', '')}",
                    author=author,
                    created_at=revised_date,
                ))
        return entries