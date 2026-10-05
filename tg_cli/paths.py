"""Filesystem paths for tg-cli.
Secrets and cache live in %APPDATA%\\tg-cli.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_app_dir() -> Path:
    """Return the base directory for tg-cli data and configuration."""
    appdata = os.getenv("APPDATA")
    if appdata:
        base = Path(appdata) / "tg-cli"
    else:
        # Fallback for non-Windows or tests
        base = Path.home() / ".config" / "tg-cli"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_db_path() -> Path:
    """Return path to SQLite database."""
    return get_app_dir() / "app.db"


def get_sessions_dir() -> Path:
    """Return directory where Telethon session files are stored."""
    sessions = get_app_dir() / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    return sessions


def get_session_path(user_id: int | str) -> Path:
    """Return session file path for a specific user ID."""
    return get_sessions_dir() / f"{user_id}.session"


def get_config_path() -> Path:
    """Return path to config file."""
    return get_app_dir() / "config.json"
