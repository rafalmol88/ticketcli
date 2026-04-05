import os
import subprocess
import tempfile
from pathlib import Path


CONFIG_PATH = Path.home() / ".ticketcli" / "config.json"


def _get_configured_editor() -> str:
    """
    Resolution order:
    1. ~/.ticketcli/config.json -> "editor"
    2. $EDITOR env var
    3. fallback: vi
    """
    if CONFIG_PATH.exists():
        try:
            import json
            cfg = json.loads(CONFIG_PATH.read_text())
            if "editor" in cfg and cfg["editor"]:
                return cfg["editor"]
        except Exception:
            pass

    return os.environ.get("EDITOR", "vi")


def open_editor(initial_text: str = "") -> str:
    """
    Opens the editor with optional initial text.
    Returns the edited content as string.
    """
    editor = _get_configured_editor()

    with tempfile.NamedTemporaryFile(suffix=".tmp", delete=False) as tf:
        temp_path = Path(tf.name)

        if initial_text:
            tf.write(initial_text.encode("utf-8"))
            tf.flush()

    try:
        subprocess.call([editor, str(temp_path)])

        content = temp_path.read_text(encoding="utf-8")

    finally:
        try:
            temp_path.unlink()
        except Exception:
            pass

    return _strip_comments(content)


def _strip_comments(text: str) -> str:
    """
    Removes lines starting with '#'
    """
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        lines.append(line)

    return "\n".join(lines).strip()