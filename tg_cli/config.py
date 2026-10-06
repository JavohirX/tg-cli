"""Configuration management for tg-cli.
Stores api_id, api_hash, and optional transcription settings in %APPDATA%\\tg-cli\\config.json.
Secrets are never written to git repositories or log files.
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any
from tg_cli.paths import get_config_path

DEFAULT_PROXY = {
    "enabled": False,
    "type": "socks5",  # "socks5", "http", "mtproto"
    "addr": "127.0.0.1",
    "port": 1080,
    "username": "",
    "password": "",
    "secret": "",
}


def _load_dotenv_if_exists() -> None:
    """Load key-value pairs from .env in current directory or user app data if not already set."""
    candidates = [Path(".env"), get_config_path().parent / ".env"]
    for path in candidates:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass


def load_config() -> dict[str, Any]:
    """Load configuration from config.json with env var fallbacks."""
    _load_dotenv_if_exists()

    config: dict[str, Any] = {
        "api_id": None,
        "api_hash": "",
        "gemini_api_key": "",
        "gemini_model": "gemini-1.5-flash",
        "proxy": dict(DEFAULT_PROXY),
    }

    path = get_config_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    if "proxy" in data and isinstance(data["proxy"], dict):
                        p = dict(DEFAULT_PROXY)
                        p.update(data["proxy"])
                        data["proxy"] = p
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

    if os.getenv("TG_PROXY_ENABLED"):
        config["proxy"]["enabled"] = os.getenv("TG_PROXY_ENABLED").lower() in ("1", "true", "yes")
    if os.getenv("TG_PROXY_ADDR"):
        config["proxy"]["addr"] = os.getenv("TG_PROXY_ADDR").strip()
    if os.getenv("TG_PROXY_PORT"):
        try:
            config["proxy"]["port"] = int(os.getenv("TG_PROXY_PORT"))
        except ValueError:
            pass
    if os.getenv("TG_PROXY_TYPE"):
        config["proxy"]["type"] = os.getenv("TG_PROXY_TYPE").strip().lower()

    return config


def save_config(
    api_id: int,
    api_hash: str,
    gemini_api_key: str = "",
    gemini_model: str = "gemini-1.5-flash",
) -> None:
    """Save api_id and api_hash to %APPDATA%\\tg-cli\\config.json."""
    cfg = load_config()
    cfg["api_id"] = int(api_id)
    cfg["api_hash"] = str(api_hash).strip()
    cfg["gemini_api_key"] = str(gemini_api_key).strip()
    cfg["gemini_model"] = str(gemini_model).strip() or "gemini-1.5-flash"

    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def get_proxy_config() -> dict[str, Any]:
    """Retrieve current proxy settings."""
    cfg = load_config()
    return cfg.get("proxy", dict(DEFAULT_PROXY))


def save_proxy_config(
    enabled: bool,
    proxy_type: str = "socks5",
    addr: str = "127.0.0.1",
    port: int = 1080,
    username: str = "",
    password: str = "",
    secret: str = "",
) -> None:
    """Persist proxy settings to config.json."""
    cfg = load_config()
    cfg["proxy"] = {
        "enabled": bool(enabled),
        "type": str(proxy_type).strip().lower(),
        "addr": str(addr).strip(),
        "port": int(port),
        "username": str(username).strip(),
        "password": str(password).strip(),
        "secret": str(secret).strip(),
    }
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def toggle_proxy() -> bool:
    """Toggle proxy enabled flag and return the new state."""
    cfg = load_config()
    proxy = cfg.get("proxy", dict(DEFAULT_PROXY))
    new_state = not proxy.get("enabled", False)
    proxy["enabled"] = new_state
    cfg["proxy"] = proxy

    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return new_state


def probe_proxy_latency(proxy_cfg: dict[str, Any] | None = None, timeout: float = 2.0) -> float | None:
    """Probe proxy endpoint connection and return roundtrip latency in milliseconds.

    Returns None if unreachable, not configured, or if probe fails.
    """
    if proxy_cfg is None:
        proxy_cfg = get_proxy_config()

    addr = proxy_cfg.get("addr")
    port = proxy_cfg.get("port")
    if not addr or not port:
        return None

    try:
        t0 = time.perf_counter()
        with socket.create_connection((addr, int(port)), timeout=timeout):
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return round(latency_ms, 1)
    except Exception:
        return None


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

