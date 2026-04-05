from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def _cache_base_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".cache"
    path = base / "ticketcli"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(namespace: str, key: str) -> Path:
    safe_key = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in key)
    return _cache_base_dir() / f"{namespace}_{safe_key}.json"


def load_cache(namespace: str, key: str, ttl_seconds: int) -> Any | None:
    path = _cache_path(namespace, key)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    fetched_at = payload.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return None

    if time.time() - float(fetched_at) > ttl_seconds:
        return None

    return payload.get("data")


def save_cache(namespace: str, key: str, data: Any) -> None:
    path = _cache_path(namespace, key)
    payload = {
        "fetched_at": time.time(),
        "data": data,
    }

    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def invalidate_cache(namespace: str, key: str) -> None:
    path = _cache_path(namespace, key)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def invalidate_all() -> int:
    """Remove every cached file.  Returns the count of deleted files."""
    cache_dir = _cache_base_dir()
    count = 0
    for path in cache_dir.glob("*.json"):
        try:
            path.unlink()
            count += 1
        except OSError:
            pass
    return count