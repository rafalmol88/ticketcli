from pathlib import Path
from typing import Dict, Tuple

CONFIG_DIR = Path.home() / ".ticketcli"


def _parse_mapping_file(path: Path) -> Dict[str, str]:
    mapping: Dict[str, str] = {}

    if not path.exists():
        return mapping

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        k, v = line.split("=", 1)
        mapping[k.strip()] = v.strip()

    return mapping


def load_user_mapping(target: str | None) -> Dict[str, str]:
    mapping: Dict[str, str] = {}

    mapping.update(_parse_mapping_file(CONFIG_DIR / "user_mapping.conf"))

    if target:
        mapping.update(_parse_mapping_file(CONFIG_DIR / f"user_mapping_{target}.conf"))

    return mapping


def resolve_user(username: str | None, target: str | None) -> Tuple[str | None, Dict[str, str]]:
    mapping = load_user_mapping(target)

    if not username:
        return None, mapping

    return mapping.get(username), mapping