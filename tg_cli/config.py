"""Configuration management for tg-cli.
Stores api_id, api_hash, and optional transcription settings in %APPDATA%\\tg-cli\\config.json.
Secrets are never written to git repositories or log files.
"""

from __future__ import annotations

import json
import os
from typing import Any
from tg_cli.paths import get_config_path


def load_config() -> dict[str, Any]:
    """Load configuration from config.json with env var fallbacks."""
    config: dict[str, Any] = {
        "api_id": None,
        "api_hash": "",
        "gemini_api_key": "",
        "gemini_model": "gemini-1.5-flash",
    }

    path = get_config_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    config.update(data)
        except Exception:
            pass

    # Environment variables take precedence if present
    env_id = os.getenv("TG_API_ID")
    if env_id:
        try:
            config["api_id"] = int(env_id)
        except ValueError:
            pass

    env_hash = os.getenv("TG_API_HASH")
    if env_hash:
        config["api_hash"] = env_hash.strip()

    env_gemini = os.getenv("GEMINI_API_KEY")
    if env_gemini:
        config["gemini_api_key"] = env_gemini.strip()

    return config


def save_config(
    api_id: int,
    api_hash: str,
    gemini_api_key: str = "",
    gemini_model: str = "gemini-1.5-flash",
) -> None:
    """Save api_id and api_hash to %APPDATA%\\tg-cli\\config.json."""
    config = {
        "api_id": int(api_id),
        "api_hash": str(api_hash).strip(),
        "gemini_api_key": str(gemini_api_key).strip(),
        "gemini_model": str(gemini_model).strip() or "gemini-1.5-flash",
    }
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def has_api_credentials() -> bool:
    cfg = load_config()
    return bool(cfg.get("api_id") and cfg.get("api_hash"))


def has_seen_help() -> bool:
    cfg = load_config()
    return bool(cfg.get("has_seen_help", False))


def mark_help_seen() -> None:
    path = get_config_path()
    cfg = load_config()
    cfg["has_seen_help"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
