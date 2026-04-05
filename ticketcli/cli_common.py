
from __future__ import annotations

import argparse
from pathlib import Path

from ticketcli.config import load_config, load_user_mappings, resolve_target
from ticketcli.handler_factory import build_handler


def add_target_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-t", "--target", help="Target name from ~/.ticketcli/targets.json")


def resolve_runtime(args: argparse.Namespace):
    config = load_config()
    target_name, target_config = resolve_target(getattr(args, "target", None))
    user_mappings = load_user_mappings(target_name)
    handler = build_handler(target_name, target_config, user_mappings)
    return config, target_name, target_config, handler


def coerce_issue_ref(handler, issue_ref: str | int) -> str:
    return handler.normalize_issue_key(issue_ref)


def ensure_output_dir(path_text: str | None) -> Path:
    path = Path(path_text or ".")
    path.mkdir(parents=True, exist_ok=True)
    return path
