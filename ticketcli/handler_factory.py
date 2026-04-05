
from __future__ import annotations

from ticketcli.handlers.azuredevops import AzureDevOpsHandler
from ticketcli.handlers.clickup import ClickUpHandler
from ticketcli.handlers.jira_cloud import JiraCloudHandler
from ticketcli.handlers.jira_server import JiraServerHandler
from ticketcli.handlers.localmock import LocalMockHandler


def build_handler(target_name: str, target_config: dict, user_mappings: dict):
    system_name = target_config["ticket_system"].strip().lower()
    registry = {
        "jira": JiraCloudHandler,
        "jira_cloud": JiraCloudHandler,
        "jira-server": JiraServerHandler,
        "jira_server": JiraServerHandler,
        "clickup": ClickUpHandler,
        "azuredevops": AzureDevOpsHandler,
        "azure_devops": AzureDevOpsHandler,
        "azure": AzureDevOpsHandler,
        "localmock": LocalMockHandler,
    }
    handler_cls = registry.get(system_name)
    if not handler_cls:
        supported = ", ".join(sorted(registry))
        raise ValueError(f"Unsupported ticket_system '{system_name}'. Supported values: {supported}")
    return handler_cls(target_name, target_config, user_mappings)
