"""Google Gemini audio transcription implementation."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any
import httpx
from tg_cli.config import load_config


class GeminiTranscriber:
    """Transcribes audio files using Google Gemini API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-3.8-flash",
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        env_key = os.getenv("GEMINI_API_KEY")
        if env_key:
            return env_key.strip()
        cfg = load_config()
        cfg_key = cfg.get("gemini_api_key")
        if cfg_key:
            return str(cfg_key).strip()
        raise ValueError(
            "Gemini API key is not configured. Set GEMINI_API_KEY env var or configure in config.json"
        )

    def transcribe_audio(self, audio_path: Path, model: str | None = None) -> str:
        api_key = self._resolve_api_key()
        active_model = model or self.model or "gemini-3.8-flash"

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        # Read and base64-encode audio file
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        b64_data = base64.b64encode(audio_bytes).decode("utf-8")

        # Determine mime type
        ext = audio_path.suffix.lower()
        if ext in (".ogg", ".oga"):
            mime_type = "audio/ogg"
        elif ext == ".mp3":
            mime_type = "audio/mp3"
        elif ext == ".wav":
            mime_type = "audio/wav"
        elif ext == ".m4a":
            mime_type = "audio/m4a"
        else:
            mime_type = "audio/ogg"

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{active_model}:generateContent"
        headers = {"Content-Type": "application/json"}
        params = {"key": api_key}

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                "Please transcribe this audio accurately. "
                                "Return only the exact transcribed speech without introductory commentary, "
                                "formatting tags, or notes."
                            )
                        },
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": b64_data,
                            }
                        },
                    ]
                }
            ]
        }

        resp = httpx.post(
            url,
            headers=headers,
            params=params,
            json=payload,
            timeout=self.timeout,
        )

        if resp.status_code != 200:
            error_msg = f"Gemini API error (status {resp.status_code}): {resp.text}"
            raise RuntimeError(error_msg)

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return ""

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        texts = [p.get("text", "") for p in parts if "text" in p]
        return "\n".join(texts).strip()
