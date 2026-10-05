"""Transcription package for tg-cli."""

from tg_cli.transcribe.base import Transcriber
from tg_cli.transcribe.gemini import GeminiTranscriber

__all__ = ["Transcriber", "GeminiTranscriber"]
