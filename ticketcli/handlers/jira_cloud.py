
from __future__ import annotations

from ticketcli.handlers.jira_common import JiraBaseHandler


class JiraCloudHandler(JiraBaseHandler):
    api_version = "3"
    assignee_payload_field = "accountId"
