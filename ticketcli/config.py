from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".ticketcli"
CONFIG_FILE = CONFIG_DIR / "config.json"
TARGETS_FILE = CONFIG_DIR / "targets.json"
USER_MAPPING_FILE = CONFIG_DIR / "user_mapping.conf"
LEGACY_USER_MAPPINGS_FILE = CONFIG_DIR / "user_mappings.conf"


DEFAULT_CONFIG = {
    "editor": "nano",
    "default_target": None,
    "require_explicit_target": True,
}


class ConfigError(RuntimeError):
    pass


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, payload: Any) -> None:
    ensure_config_dir()
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _ensure_text_file(path: Path, content: str = "") -> None:
    ensure_config_dir()
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def target_user_mapping_file(target_name: str) -> Path:
    return CONFIG_DIR / f"user_mapping_{target_name}.conf"


def bootstrap_files() -> None:
    ensure_config_dir()
    if not CONFIG_FILE.exists():
        _save_json(CONFIG_FILE, DEFAULT_CONFIG)
    if not TARGETS_FILE.exists():
        _save_json(TARGETS_FILE, {})
    _ensure_text_file(USER_MAPPING_FILE, "# one mapping per line, for example:\n# jan=em92863\n")


def load_config() -> dict[str, Any]:
    bootstrap_files()
    config = DEFAULT_CONFIG.copy()
    config.update(_load_json(CONFIG_FILE, {}))
    return config


def save_config(config: dict[str, Any]) -> None:
    _save_json(CONFIG_FILE, config)


def load_targets() -> dict[str, Any]:
    bootstrap_files()
    return _load_json(TARGETS_FILE, {})


def save_targets(targets: dict[str, Any]) -> None:
    _save_json(TARGETS_FILE, targets)


def _load_simple_mapping_file(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not path.exists():
        return mapping

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            mapping[key] = value
    return mapping


def load_user_mappings(target_name: str | None = None) -> dict[str, str]:
    bootstrap_files()
    merged: dict[str, str] = {}
    merged.update(_load_simple_mapping_file(USER_MAPPING_FILE))

    # backward-compatible legacy JSON support if the old file still exists
    if LEGACY_USER_MAPPINGS_FILE.exists():
        legacy = _load_json(LEGACY_USER_MAPPINGS_FILE, {})
        if isinstance(legacy, dict):
            for key, value in legacy.items():
                if isinstance(value, str):
                    merged.setdefault(key, value)

    if target_name:
        merged.update(_load_simple_mapping_file(target_user_mapping_file(target_name)))

    return merged


def resolve_target(target_name: str | None) -> tuple[str, dict[str, Any]]:
    config = load_config()
    targets = load_targets()

    effective = target_name
    if not effective:
        if config.get("require_explicit_target", True):
            raise ConfigError(
                "No target provided and explicit target mode is enabled. Use --target or set a default target."
            )
        effective = config.get("default_target")

    if not effective:
        raise ConfigError("No target could be resolved. Set a default target or pass --target.")

    target = targets.get(effective)
    if not target:
        raise ConfigError(f"Unknown target: {effective}")

    if "ticket_system" not in target:
        raise ConfigError(f"Target '{effective}' is missing required key: ticket_system")

    return effective, target


def set_default_target(target_name: str | None, require_explicit_target: bool | None = None) -> dict[str, Any]:
    config = load_config()
    targets = load_targets()

    if target_name is not None:
        if target_name and target_name not in targets:
            raise ConfigError(f"Unknown target: {target_name}")
        config["default_target"] = target_name or None

    if require_explicit_target is not None:
        config["require_explicit_target"] = require_explicit_target

    save_config(config)
    return config
