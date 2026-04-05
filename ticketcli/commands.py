"""Legacy compatibility wrappers.

Prefer `ticketcli.cli` and the `ticketcli` console script.
"""
from __future__ import annotations

from ticketcli.cli import main


def main_guard(entrypoint):
    return entrypoint()


def create_issue_main() -> None:
    main(["add"])


def edit_issue_main() -> None:
    main(["edit"])


def add_comment_main() -> None:
    main(["comment"])


def show_details_main() -> None:
    main(["show"])


def download_attachments_main() -> None:
    main(["attachments"])


def list_issues_main() -> None:
    main(["list"])


def set_target_main() -> None:
    main(["target"])


def list_targets_main() -> None:
    main(["targets"])
