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