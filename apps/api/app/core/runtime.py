"""Runtime toggles an operator can flip without a restart.

Only the keys in ``EDITABLE`` may change at runtime; everything else stays a
deploy-time setting. Values are persisted next to the database so a restart
keeps what the panel shows.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.core.config import get_settings

log = logging.getLogger(__name__)

EDITABLE = ("mcp_tool_router", "mcp_cite_external")
_FILE = "runtime.json"


def _path() -> Path:
    return Path(get_settings().data_dir) / _FILE


def load_overrides() -> dict[str, bool]:
    path = _path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001 - a corrupt file must not block startup
        log.warning("ignoring unreadable %s: %s", path, exc)
        return {}
    return {key: value for key, value in raw.items() if key in EDITABLE and isinstance(value, bool)}


def apply_overrides() -> dict[str, bool]:
    overrides = load_overrides()
    settings = get_settings()
    for key, value in overrides.items():
        setattr(settings, key, value)
    return overrides


def current() -> dict[str, bool]:
    settings = get_settings()
    return {key: bool(getattr(settings, key)) for key in EDITABLE}


def update(changes: dict[str, Any]) -> dict[str, bool]:
    """Validate, apply in-process, then persist. Raises ``ValueError`` on bad input."""
    if not changes:
        raise ValueError("no settings supplied")
    settings = get_settings()
    applied: dict[str, bool] = {}
    for key, value in changes.items():
        if key not in EDITABLE:
            raise ValueError(f"unknown setting: {key}")
        if not isinstance(value, bool):
            raise TypeError(f"{key} must be a boolean")
        setattr(settings, key, value)
        applied[key] = value

    stored = load_overrides()
    stored.update(applied)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), "utf-8")
    return current()
