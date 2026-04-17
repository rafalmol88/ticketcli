# base.py

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ticketcli.models import Issue, IssueListItem, ChangelogEntry
from ticketcli.utils.user_select import choose_mapped_user_interactively, choose_mapped_users_interactively


class TicketHandler(ABC):
    def __init__(self, target_name: str, target_config: dict[str, Any], user_mappings: dict[str, str]):
        self.target_name = target_name
        self.target_config = target_config
        self.user_mappings = user_mappings

    def normalize_issue_key(self, issue_ref: str | int) -> str:
        issue_ref = str(issue_ref).strip()
        if "-" in issue_ref:
            return issue_ref
        project_base = self.target_config.get("project_base")
        if not project_base:
            raise ValueError(
                f"Target '{self.target_name}' has no project_base configured, so shorthand issue numbers cannot be resolved."
            )
        return f"{project_base}-{issue_ref}"

    def available_user_mappings(self) -> dict[str, str]:
        return dict(self.user_mappings or {})

    def map_human_user(self, name: str | None) -> str | None:
        if not name:
            return None
        return self.available_user_mappings().get(name)

    def reverse_map_user(self, system_id: str | None) -> str | None:
        """Reverse-lookup: given a system identifier, return the human alias."""
        if not system_id:
            return None
        for human, sys_id in self.available_user_mappings().items():
            if sys_id == system_id:
                return human
        return None

    def resolve_assignee(self, name: str | None) -> str | None:
        mapping = self.available_user_mappings()

        if not mapping:
            print("No user mappings configured.")
            return None

        if name:
            exact = self.map_human_user(name)
            if exact:
                return exact
            print(f"Unknown assignee: {name}")

        return choose_mapped_user_interactively(mapping, initial_query=name)

    def resolve_assignees(self, current_system_ids: list[str] | None = None) -> list[str] | None:
        """Interactively select zero or more assignees via a checkbox prompt.

        Pre-checks entries matching *current_system_ids*.
        Returns the selected list of system IDs, or ``None`` if cancelled.
        """
        mapping = self.available_user_mappings()
        if not mapping:
            print("No user mappings configured.")
            return None
        return choose_mapped_users_interactively(mapping, current_system_ids=current_system_ids)

    @abstractmethod
    def create_issue(self, summary: str, description: str, **kwargs: Any):
        raise NotImplementedError

    @abstractmethod
    def edit_issue(self, issue_key: str, summary: str | None = None, description: str | None = None, **kwargs: Any):
        raise NotImplementedError

    @abstractmethod
    def add_comment(self, issue_key: str, comment: str, **kwargs: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_issue_details(self, issue_key: str):
        raise NotImplementedError

    @abstractmethod
    def download_attachments(self, issue_key: str, attachment_ids: list[str], output_dir: Path):
        raise NotImplementedError

    @abstractmethod
    def list_issues(self, created_by_me: bool = False, all_unresolved: bool = False) -> list[IssueListItem]:
        raise NotImplementedError

    def upload_attachment(self, issue_key: str, file_path: Path) -> None:
        """Upload a file as an attachment to an issue. Override in subclasses."""
        raise NotImplementedError(f"upload_attachment is not supported by {type(self).__name__}")

    def delete_attachment(self, issue_key: str, attachment_id: str) -> None:
        """Delete an attachment from an issue. Override in subclasses."""
        raise NotImplementedError(f"delete_attachment is not supported by {type(self).__name__}")

    def list_transitions(self, issue_key: str) -> list[str]:
        """Return available status/transition names for an issue. Override in subclasses."""
        return []

    def transition_issue(self, issue_key: str, target_status: str) -> None:
        """Change the status/state of an issue. Override in subclasses."""
        raise NotImplementedError(f"transition_issue is not supported by {type(self).__name__}")

    def pin_comment(self, issue_key: str, comment_id: str) -> None:
        """Pin / highlight a comment. Override in subclasses."""
        raise NotImplementedError(f"pin_comment is not supported by {type(self).__name__}")

    def unpin_comment(self, issue_key: str, comment_id: str) -> None:
        """Unpin a comment. Override in subclasses."""
        raise NotImplementedError(f"unpin_comment is not supported by {type(self).__name__}")

    def get_issue_changelog(self, issue_key: str) -> list[ChangelogEntry]:
        """Return the change history of an issue. Override in subclasses."""
        return []