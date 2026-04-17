from __future__ import annotations

from typing import Mapping

import questionary


KEEP_UNCHANGED = "__KEEP_UNCHANGED__"
UNASSIGN = "__UNASSIGN__"


def choose_mapped_user_interactively(
    mapping: Mapping[str, str],
    initial_query: str | None = None,
    allow_keep_unchanged: bool = True,
    allow_unassign: bool = False,
) -> str | None:
    names = sorted(mapping.keys())
    choices: list[questionary.Choice | str] = []

    if allow_keep_unchanged:
        choices.append(questionary.Choice("Keep unchanged", value=KEEP_UNCHANGED))

    for name in names:
        system_value = mapping[name]
        label = f"{name} -> {system_value}" if system_value and system_value != name else name
        choices.append(questionary.Choice(label, value=system_value))

    if allow_unassign:
        choices.append(questionary.Choice("Unassign", value=UNASSIGN))

    result = questionary.select(
        "Select assignee",
        choices=choices,
        use_shortcuts=True,
        use_indicator=True,
        pointer="➜",
        qmark="",
    ).ask()

    if result is None or result == KEEP_UNCHANGED:
        return None

    if result == UNASSIGN:
        return None

    return str(result)


def choose_mapped_users_interactively(
    mapping: Mapping[str, str],
    current_system_ids: list[str] | None = None,
) -> list[str] | None:
    """Show a checkbox prompt to select zero or more assignees.

    Pre-checks entries whose system value appears in *current_system_ids*.
    Returns a (possibly empty) list of system IDs, or ``None`` if the user
    cancelled (Ctrl+C / Esc).
    """
    names = sorted(mapping.keys())
    if not names:
        print("No user mappings configured.")
        return None

    current_set = set(current_system_ids or [])

    choices: list[questionary.Choice] = []
    for name in names:
        system_value = mapping[name]
        label = f"{name} -> {system_value}" if system_value and system_value != name else name
        checked = system_value in current_set
        choices.append(questionary.Choice(label, value=system_value, checked=checked))

    result = questionary.checkbox(
        "Select assignees",
        choices=choices,
        instruction="(Space to select, ↑↓ to navigate, Enter to confirm, Ctrl+C to cancel)",
        pointer="➜",
        qmark="",
    ).ask()

    if result is None:
        return None

    return [str(r) for r in result]
