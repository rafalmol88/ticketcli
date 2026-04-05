from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ticketcli.handlers.base import TicketHandler
from ticketcli.models import Attachment, ChangelogEntry, Comment, Issue, IssueLink, IssueListItem


class GitHubHandler(TicketHandler):
    """Handler for GitHub Issues."""

    def __init__(self, target_name: str, target_config: dict[str, Any], user_mappings: dict[str, Any]):
        super().__init__(target_name, target_config, user_mappings)

        self.owner = str(target_config.get("owner", "")).strip()
        self.repo = str(target_config.get("repo", "")).strip()
        if not self.owner or not self.repo:
            raise ValueError(
                f"Target '{target_name}' must define 'owner' and 'repo' for GitHub Issues."
            )

        self.base_url = str(target_config.get("base_url", "https://api.github.com")).rstrip("/")
        self.timeout = int(target_config.get("timeout_seconds", 60))
        self.max_results = int(target_config.get("list_issues_max_results", 100))

        auth = target_config.get("auth", {})
        token_env = auth.get("token_env")
        token = os.getenv(token_env) if token_env else None
        if not token:
            raise ValueError(
                f"GitHub token not available. Set env var from auth.token_env for target '{target_name}'."
            )

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _repo_url(self, suffix: str = "") -> str:
        base = f"{self.base_url}/repos/{self.owner}/{self.repo}"
        return f"{base}/{suffix.lstrip('/')}" if suffix else base

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        response = self.session.request(method, url, **kwargs)
        if response.status_code >= 400:
            detail = response.text.strip()[:500]
            raise RuntimeError(
                f"GitHub API {method} {url} failed with {response.status_code}: {detail}"
            )
        return response

    def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a GitHub GraphQL query and return the 'data' payload."""
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        response = self._request("POST", f"{self.base_url}/graphql", json=payload)
        body = response.json()
        if body.get("errors"):
            msgs = "; ".join(e.get("message", "?") for e in body["errors"])
            raise RuntimeError(f"GitHub GraphQL error: {msgs}")
        return body.get("data", {})

    def _issue_number(self, issue_key: str) -> int:
        """Extract the numeric issue number from 'PROJ-42' or bare '42'."""
        key = str(issue_key).strip()
        if "-" in key:
            return int(key.rsplit("-", 1)[-1])
        return int(key)

    def _make_key(self, number: int) -> str:
        project_base = self.target_config.get("project_base")
        if project_base:
            return f"{project_base}-{number}"
        return str(number)

    @staticmethod
    def _parse_iso(raw: Any) -> str | None:
        if not raw:
            return None
        return str(raw)

    def _fetch_current_user(self) -> str:
        response = self._request("GET", f"{self.base_url}/user")
        return response.json().get("login", "")

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_issue(
        self,
        raw: dict[str, Any],
        comments_raw: list[dict[str, Any]] | None = None,
        pinned_ids: set[str] | None = None,
    ) -> Issue:
        number = raw.get("number", 0)
        key = self._make_key(number)

        # Labels
        labels = [lbl.get("name", "") for lbl in (raw.get("labels") or []) if lbl.get("name")]

        # Assignee (first one)
        assignees_raw = raw.get("assignees") or []
        assignee = None
        if assignees_raw:
            assignee = assignees_raw[0].get("login")
        elif raw.get("assignee"):
            assignee = raw["assignee"].get("login")

        creator = (raw.get("user") or {}).get("login", "")

        # Comments
        _pinned = pinned_ids or set()
        comments: list[Comment] = []
        for item in comments_raw or []:
            user = item.get("user") or {}
            cid = str(item.get("id", ""))
            comments.append(
                Comment(
                    id=cid,
                    author=user.get("login") or "unknown",
                    body=item.get("body") or "",
                    created_at=self._parse_iso(item.get("created_at")),
                    pinned=cid in _pinned,
                )
            )

        # Issue links: parse "Fixes #123", "Closes #456", "Related to #789" from body
        links = self._parse_issue_refs(raw.get("body") or "")

        return Issue(
            key=key,
            summary=raw.get("title", ""),
            description=raw.get("body") or "",
            status=raw.get("state", ""),
            assignee=assignee,
            creator=creator,
            id=str(raw.get("id", "")),
            labels=labels,
            attachments=[],  # GitHub doesn't have first-class attachments on issues
            comments=comments,
            worklogs=[],  # GitHub has no worklog / time-tracking concept
            worklogs_available=False,
            links=links,
            raw=raw,
        )

    def _fetch_pinned_comment_ids(self, issue_number: int) -> set[str]:
        """Return the set of REST databaseId strings for pinned comments via GraphQL."""
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            issue(number: $number) {
              comments(first: 100) {
                nodes { databaseId isPinned }
                pageInfo { hasNextPage endCursor }
              }
            }
          }
        }
        """
        pinned: set[str] = set()
        cursor: str | None = None

        for _ in range(20):  # pagination safety limit
            q = query
            variables: dict[str, Any] = {
                "owner": self.owner,
                "repo": self.repo,
                "number": issue_number,
            }
            if cursor:
                # Inject 'after' argument into the comments() call
                q = q.replace(
                    "comments(first: 100)",
                    f'comments(first: 100, after: "{cursor}")',
                )
            try:
                data = self._graphql(q, variables)
            except Exception:
                break

            comments_data = (
                data.get("repository", {})
                .get("issue", {})
                .get("comments", {})
            )
            for node in comments_data.get("nodes", []):
                if node.get("isPinned"):
                    pinned.add(str(node["databaseId"]))

            page_info = comments_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return pinned

    def _comment_node_id(self, comment_id: str) -> str:
        """Fetch the GraphQL node_id for a comment given its REST databaseId."""
        url = self._repo_url(f"issues/comments/{comment_id}")
        resp = self._request("GET", url)
        node_id = resp.json().get("node_id", "")
        if not node_id:
            raise RuntimeError(f"Could not resolve node_id for comment {comment_id}")
        return node_id

    @staticmethod
    def _parse_issue_refs(body: str) -> list[IssueLink]:
        """Extract issue references like 'Fixes #123', 'Closes #456' from body text."""
        import re

        links: list[IssueLink] = []
        seen: set[str] = set()
        # Patterns: "fixes #N", "closes #N", "resolves #N", "related to #N", plain "#N"
        for match in re.finditer(
            r"(?:(?P<verb>fix(?:es|ed)?|clos(?:es|ed)?|resolv(?:es|ed)?|related\s+to)\s+)?#(?P<num>\d+)",
            body,
            re.IGNORECASE,
        ):
            num = match.group("num")
            if num in seen:
                continue
            seen.add(num)
            verb = (match.group("verb") or "references").strip().lower()
            links.append(IssueLink(link_type=verb, outward_key=f"#{num}"))
        return links

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_issue(self, summary: str, description: str, **kwargs: Any) -> Issue:
        payload: dict[str, Any] = {
            "title": summary,
            "body": description,
        }
        assignee = kwargs.get("assignee")
        if assignee:
            payload["assignees"] = [str(assignee)]
        labels = kwargs.get("labels")
        if labels:
            payload["labels"] = list(labels)

        response = self._request("POST", self._repo_url("issues"), json=payload)
        raw = response.json()
        return self._parse_issue(raw)

    def edit_issue(
        self,
        issue_key: str,
        summary: str | None = None,
        description: str | None = None,
        **kwargs: Any,
    ) -> Issue:
        number = self._issue_number(issue_key)
        payload: dict[str, Any] = {}
        if summary is not None:
            payload["title"] = summary
        if description is not None:
            payload["body"] = description
        if kwargs.get("assignee"):
            payload["assignees"] = [str(kwargs["assignee"])]
        labels = kwargs.get("labels")
        if labels is not None:
            payload["labels"] = list(labels)

        if payload:
            self._request("PATCH", self._repo_url(f"issues/{number}"), json=payload)
        return self.get_issue_details(issue_key)

    def add_comment(self, issue_key: str, comment: str, **kwargs: Any) -> None:
        number = self._issue_number(issue_key)
        self._request(
            "POST",
            self._repo_url(f"issues/{number}/comments"),
            json={"body": comment},
        )

    def get_issue_details(self, issue_key: str) -> Issue:
        number = self._issue_number(issue_key)
        raw = self._request("GET", self._repo_url(f"issues/{number}")).json()
        comments_raw = self._fetch_all_comments(number)
        pinned_ids = self._fetch_pinned_comment_ids(number)
        return self._parse_issue(raw, comments_raw=comments_raw, pinned_ids=pinned_ids)

    def _fetch_all_comments(self, number: int) -> list[dict[str, Any]]:
        """Paginate through all comments on an issue."""
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self._request(
                "GET",
                self._repo_url(f"issues/{number}/comments"),
                params={"per_page": 100, "page": page},
            )
            batch = response.json()
            if not batch:
                break
            results.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return results

    def download_attachments(self, issue_key: str, attachment_ids: list[str], output_dir: Path) -> list[Path]:
        # GitHub Issues don't have first-class attachments.
        # Files are typically embedded as markdown image/link in the body.
        print("GitHub Issues do not support first-class attachments.")
        return []

    def upload_attachment(self, issue_key: str, file_path: Path) -> None:
        raise NotImplementedError(
            "GitHub Issues do not support file attachments via the API. "
            "Upload the file to a GitHub release or embed a link in the issue body."
        )

    def delete_attachment(self, issue_key: str, attachment_id: str) -> None:
        raise NotImplementedError("GitHub Issues do not support first-class attachments.")

    def list_issues(
        self, created_by_me: bool = False, all_unresolved: bool = False
    ) -> list[IssueListItem]:
        params: dict[str, Any] = {
            "state": "open",
            "per_page": min(self.max_results, 100),
            "page": 1,
        }

        if not all_unresolved:
            if created_by_me:
                params["creator"] = self._fetch_current_user()
            else:
                params["assignee"] = self._fetch_current_user()

        results: list[IssueListItem] = []
        max_pages = (self.max_results + 99) // 100

        for page in range(1, max_pages + 1):
            params["page"] = page
            response = self._request("GET", self._repo_url("issues"), params=params)
            items = response.json()
            if not items:
                break

            for raw in items:
                # Skip pull requests (GitHub returns PRs under /issues too)
                if raw.get("pull_request"):
                    continue
                number = raw.get("number", 0)
                assignees = raw.get("assignees") or []
                assignee_text = assignees[0].get("login") if assignees else None
                labels = [lbl.get("name", "") for lbl in (raw.get("labels") or []) if lbl.get("name")]
                results.append(
                    IssueListItem(
                        key=self._make_key(number),
                        summary=raw.get("title", ""),
                        assignee=assignee_text,
                        status=raw.get("state", ""),
                        labels=labels,
                    )
                )

            if len(items) < int(params["per_page"]):
                break

        return results

    # ------------------------------------------------------------------
    # Transitions (open / closed)
    # ------------------------------------------------------------------

    def list_transitions(self, issue_key: str) -> list[str]:
        """GitHub issues have two states: open and closed."""
        return ["open", "closed"]

    def transition_issue(self, issue_key: str, target_status: str) -> None:
        number = self._issue_number(issue_key)
        target_lower = target_status.lower()
        # Map common close-status names to GitHub's "closed"
        if target_lower in {"closed", "done", "resolved", "complete", "completed"}:
            state = "closed"
        elif target_lower in {"open", "reopen", "reopened", "to do", "todo"}:
            state = "open"
        else:
            raise RuntimeError(
                f"GitHub only supports 'open' and 'closed' states. Got: '{target_status}'"
            )
        self._request(
            "PATCH",
            self._repo_url(f"issues/{number}"),
            json={"state": state},
        )

    # ------------------------------------------------------------------
    # Pin / unpin comments (via GitHub's native issue-comment pinning)
    # ------------------------------------------------------------------

    def pin_comment(self, issue_key: str, comment_id: str) -> None:
        """Pin a comment using the GitHub GraphQL pinIssueComment mutation."""
        node_id = self._comment_node_id(comment_id)
        self._graphql(
            """
            mutation($id: ID!) {
              pinIssueComment(input: {issueCommentId: $id}) {
                issueComment { isPinned }
              }
            }
            """,
            variables={"id": node_id},
        )

    def unpin_comment(self, issue_key: str, comment_id: str) -> None:
        """Unpin a comment using the GitHub GraphQL unpinIssueComment mutation."""
        node_id = self._comment_node_id(comment_id)
        self._graphql(
            """
            mutation($id: ID!) {
              unpinIssueComment(input: {issueCommentId: $id}) {
                issueComment { isPinned }
              }
            }
            """,
            variables={"id": node_id},
        )

    # ------------------------------------------------------------------
    # Changelog (via timeline events)
    # ------------------------------------------------------------------

    def get_issue_changelog(self, issue_key: str) -> list[ChangelogEntry]:
        """Fetch issue timeline events and return changelog entries."""
        number = self._issue_number(issue_key)
        entries: list[ChangelogEntry] = []
        page = 1
        while True:
            try:
                response = self._request(
                    "GET",
                    self._repo_url(f"issues/{number}/timeline"),
                    params={"per_page": 100, "page": page},
                )
            except Exception:
                break
            events = response.json()
            if not events:
                break

            for event in events:
                event_type = event.get("event", "")
                actor = (event.get("actor") or {}).get("login", "")
                created = self._parse_iso(event.get("created_at"))

                if event_type == "labeled":
                    entries.append(ChangelogEntry(
                        field="labels",
                        to_value=(event.get("label") or {}).get("name"),
                        author=actor,
                        created_at=created,
                    ))
                elif event_type == "unlabeled":
                    entries.append(ChangelogEntry(
                        field="labels",
                        from_value=(event.get("label") or {}).get("name"),
                        author=actor,
                        created_at=created,
                    ))
                elif event_type == "assigned":
                    entries.append(ChangelogEntry(
                        field="assignee",
                        to_value=(event.get("assignee") or {}).get("login"),
                        author=actor,
                        created_at=created,
                    ))
                elif event_type == "unassigned":
                    entries.append(ChangelogEntry(
                        field="assignee",
                        from_value=(event.get("assignee") or {}).get("login"),
                        author=actor,
                        created_at=created,
                    ))
                elif event_type == "closed":
                    entries.append(ChangelogEntry(
                        field="status",
                        from_value="open",
                        to_value="closed",
                        author=actor,
                        created_at=created,
                    ))
                elif event_type == "reopened":
                    entries.append(ChangelogEntry(
                        field="status",
                        from_value="closed",
                        to_value="open",
                        author=actor,
                        created_at=created,
                    ))
                elif event_type == "renamed":
                    rename = event.get("rename") or {}
                    entries.append(ChangelogEntry(
                        field="summary",
                        from_value=rename.get("from"),
                        to_value=rename.get("to"),
                        author=actor,
                        created_at=created,
                    ))
                elif event_type in ("cross-referenced", "referenced"):
                    source = event.get("source", {}).get("issue", {})
                    if source.get("number"):
                        entries.append(ChangelogEntry(
                            field="reference",
                            to_value=f"#{source['number']}",
                            author=actor,
                            created_at=created,
                        ))

            if len(events) < 100:
                break
            page += 1

        return entries
