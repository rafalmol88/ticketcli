
from __future__ import annotations

import os

import requests
from requests.auth import HTTPBasicAuth

from ticketcli.handlers.jira_common import JiraBaseHandler


class JiraCloudHandler(JiraBaseHandler):
    api_version = "3"
    assignee_payload_field = "accountId"

    def _build_session(self, target_config: dict[str, object]) -> requests.Session:
        auth = target_config.get("auth", {}) if isinstance(target_config, dict) else {}
        email_env = auth.get("email_env") if isinstance(auth, dict) else None
        token_env = auth.get("token_env") if isinstance(auth, dict) else None
        email = os.getenv(email_env) if email_env else None
        token = os.getenv(token_env) if token_env else None
        if not email or not token:
            raise ValueError(
                f"Jira Cloud credentials are not available. Set env vars from auth.email_env/auth.token_env for target '{self.target_name}'."
            )
        session = requests.Session()
        session.auth = HTTPBasicAuth(email, token)
        return session
