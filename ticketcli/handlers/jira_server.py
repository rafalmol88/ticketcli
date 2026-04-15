
from __future__ import annotations

from typing import Any

from ticketcli.handlers.jira_common import JiraBaseHandler


class JiraServerHandler(JiraBaseHandler):
    api_version = "2"
    assignee_payload_field = "name"

    def _format_description_for_write(self, description: str) -> Any:
        return description

    def _parse_description_for_read(self, description: Any) -> str:
        if description is None:
            return ""
        if isinstance(description, str):
            return description.strip()
        return super()._parse_description_for_read(description)

    def _format_comment_for_write(self, comment: str) -> Any:
        return comment

    def _parse_comment_for_read(self, body: Any) -> str:
        if body is None:
            return ""
        if isinstance(body, str):
            return body.strip()
        return super()._parse_comment_for_read(body)
