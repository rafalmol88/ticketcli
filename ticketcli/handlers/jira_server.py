
from __future__ import annotations

import os
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from ticketcli.handlers.jira_common import JiraBaseHandler


class JiraServerHandler(JiraBaseHandler):
    api_version = "2"
    assignee_payload_field = "name"

    def _build_session(self, target_config: dict[str, Any]) -> requests.Session:
        auth = target_config.get("auth", {})
        username_env = auth.get("username_env") or auth.get("email_env")
        password_env = auth.get("password_env") or auth.get("token_env")
        username = os.getenv(username_env) if username_env else None
        password = os.getenv(password_env) if password_env else None
        if not username or not password:
            raise ValueError(
                f"Jira Server credentials are not available. Set env vars from auth.username_env/auth.password_env for target '{self.target_name}'."
            )
        session = requests.Session()
        session.auth = HTTPBasicAuth(username, password)
        return session

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
