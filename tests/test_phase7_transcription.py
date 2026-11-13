"""Tests for Phase 7: Voice transcription with Gemini, caching, forced re-transcription, and temp file cleanup."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from textual.app import App, ComposeResult
from tg_cli.domain.models import Account, Chat, ChatKind, Message, MessageKind, Transcript
from tg_cli.telegram.fake import FakeGateway
from tg_cli.transcribe.base import Transcriber
from tg_cli.transcribe.gemini import GeminiTranscriber
from tg_cli.ui.screens.messages import MessagesScreen


class MockTranscriber:
    """Mock transcriber tracking calls and file existence."""

    def __init__(self, response_text: str = "Hello from voice transcription") -> None:
        self.call_count: int = 0
        self.response_text: str = response_text
        self.last_audio_path: Path | None = None
        self.should_fail: bool = False

    def transcribe_audio(self, audio_path: Path, model: str | None = None) -> str:
        self.call_count += 1
        self.last_audio_path = audio_path
        # Confirm that the file actually exists during execution
        assert audio_path.exists(), "Audio file must exist when transcriber is called"
        if self.should_fail:
            raise RuntimeError("API quota exceeded")
        return self.response_text


class TranscribePilotApp(App):
    def __init__(self, gateway: FakeGateway, account: Account, chat: Chat, transcriber: Transcriber) -> None:
        super().__init__()
        self.gateway = gateway
        self.account = account
        self.chat = chat
        self.transcriber = transcriber

    def compose(self) -> ComposeResult:
        return []

    def on_mount(self) -> None:
        self.push_screen(
            MessagesScreen(gateway=self.gateway, account=self.account, chat=self.chat)
        )


async def _transcription_done(app, pilot) -> None:
    """The work runs on a thread; its result comes back through call_from_thread."""
    await app.workers.wait_for_complete()
    await pilot.pause()


class SlowTranscriber(MockTranscriber):
    """Blocks inside transcribe_audio until the test lets it go."""

    def __init__(self) -> None:
        super().__init__(response_text="done")
        self.started = threading.Event()
        self.release = threading.Event()

    def transcribe_audio(self, audio_path: Path, model: str | None = None) -> str:
        self.started.set()
        self.release.wait(timeout=5)
        return super().transcribe_audio(audio_path, model)


@pytest.mark.asyncio
async def test_transcription_leaves_the_ui_responsive():
    gw = FakeGateway(chat_count=3)
    acc = gw.get_accounts()[0]
    chat = gw.get_chats(acc.user_id)[0]
    gw.messages[(acc.user_id, chat.chat_id)].append(
        Message(
            account_id=acc.user_id,
            chat_id=chat.chat_id,
            message_id=556,
            sender_name="Alice",
            kind=MessageKind.VOICE,
            plain_text="[voice]",
        )
    )
    slow = SlowTranscriber()
    app = TranscribePilotApp(gw, acc, chat, slow)

    async with app.run_test() as pilot:
        screen = app.screen
        screen.message_list.cursor = next(
            i for i, it in enumerate(screen.message_list.items)
            if getattr(it, "message_id", None) == 556
        )
        await pilot.press("t")
        assert slow.started.wait(timeout=5)

        # A second press while it runs does not start another one.
        await pilot.press("t")
        await pilot.pause()
        assert "Already transcribing" in screen.status_bar.status_message

        # Keys still work while Gemini is busy.
        await pilot.press("k")
        await pilot.pause()
        assert screen.message_list.get_focused_item().message_id != 556

        slow.release.set()
        await _transcription_done(app, pilot)
        assert slow.call_count == 1
        assert gw.get_transcript(acc.user_id, chat.chat_id, 556).text == "done"


@pytest.mark.asyncio
async def test_transcription_caching_and_cleanup():
    gw = FakeGateway(chat_count=3)
    acc = gw.get_accounts()[0]
    chat = gw.get_chats(acc.user_id)[0]

    # Add a voice message
    voice_msg = Message(
        account_id=acc.user_id,
        chat_id=chat.chat_id,
        message_id=555,
        sender_id=acc.user_id,
        sender_name="Alice",
        kind=MessageKind.VOICE,
        plain_text="[voice]",
        outgoing=False,
    )
    gw.messages[(acc.user_id, chat.chat_id)].append(voice_msg)

    mock = MockTranscriber(response_text="I'll be home around eight.")
    app = TranscribePilotApp(gw, acc, chat, mock)

    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, MessagesScreen)

        # Move cursor to voice message (at the bottom)
        target_idx = next(
            i for i, it in enumerate(screen.message_list.items)
            if getattr(it, "message_id", None) == 555
        )
        screen.message_list.cursor = target_idx

        # 1. First 't' -> Should call network/transcriber once
        await pilot.press("t")
        await _transcription_done(app, pilot)

        assert mock.call_count == 1
        # Check transcript saved in gateway
        cached = gw.get_transcript(acc.user_id, chat.chat_id, 555)
        assert cached is not None
        assert cached.text == "I'll be home around eight."

        # Verify temp audio file is gone afterward
        assert mock.last_audio_path is not None
        assert not mock.last_audio_path.exists(), "Temporary audio file must be deleted after transcription"

        # 2. Second 't' on same message -> Should NOT call network again
        await pilot.press("t")
        await _transcription_done(app, pilot)

        assert mock.call_count == 1  # Still 1! Used cached transcript

        # 3. 'T' (shift+t) -> Forced re-transcription -> MUST call network again
        await pilot.press("T")
        await _transcription_done(app, pilot)

        assert mock.call_count == 2  # Incremented to 2!
        # Verify temp audio file is gone again
        assert not mock.last_audio_path.exists(), "Temporary audio file must be deleted after forced re-transcription"


@pytest.mark.asyncio
async def test_transcription_failure_and_retry():
    gw = FakeGateway(chat_count=3)
    acc = gw.get_accounts()[0]
    chat = gw.get_chats(acc.user_id)[0]

    voice_msg = Message(
        account_id=acc.user_id,
        chat_id=chat.chat_id,
        message_id=777,
        sender_id=acc.user_id,
        sender_name="Bob",
        kind=MessageKind.VOICE,
        plain_text="[voice]",
        outgoing=False,
    )
    gw.messages[(acc.user_id, chat.chat_id)].append(voice_msg)

    mock = MockTranscriber(response_text="Success on retry")
    mock.should_fail = True
    app = TranscribePilotApp(gw, acc, chat, mock)

    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, MessagesScreen)

        target_idx = next(
            i for i, it in enumerate(screen.message_list.items)
            if getattr(it, "message_id", None) == 777
        )
        screen.message_list.cursor = target_idx

        # 1. First 't' fails
        await pilot.press("t")
        await _transcription_done(app, pilot)

        assert mock.call_count == 1
        failed_rec = gw.get_transcript(acc.user_id, chat.chat_id, 777)
        assert failed_rec is not None
        assert "API quota exceeded" in failed_rec.error
        # Temp file still cleaned up even on failure
        assert mock.last_audio_path is not None
        assert not mock.last_audio_path.exists()

        # 2. Fix the error and press 't' again to retry
        mock.should_fail = False
        await pilot.press("t")
        await _transcription_done(app, pilot)

        assert mock.call_count == 2
        success_rec = gw.get_transcript(acc.user_id, chat.chat_id, 777)
        assert success_rec is not None
        assert success_rec.text == "Success on retry"
        assert not mock.last_audio_path.exists()


def test_gemini_transcriber_missing_key(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("tg_cli.config._load_dotenv_if_exists", lambda: None)
    fake_config = tmp_path / "config.json"
    fake_config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("tg_cli.config.get_config_path", lambda: fake_config)

    transcriber = GeminiTranscriber(api_key=None)
    dummy_file = tmp_path / "dummy.ogg"
    dummy_file.write_bytes(b"dummy")

    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        transcriber.transcribe_audio(dummy_file)
