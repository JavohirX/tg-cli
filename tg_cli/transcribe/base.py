"""Base protocol for audio transcription services."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Transcriber(Protocol):
    """Protocol for transcribing audio files."""

    def transcribe_audio(self, audio_path: Path, model: str | None = None) -> str:
        """Transcribe an audio file and return the transcript text."""
        ...
