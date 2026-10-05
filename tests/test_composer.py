"""Tests for Composer widget."""

import pytest
from textual.app import App, ComposeResult
from tg_cli.ui.composer import Composer


class ComposerTestApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.composer = Composer()
        self.submitted_messages: list[tuple[str, int | None, int | None]] = []
        self.errors: list[str] = []
        self.cancelled = False

    def compose(self) -> ComposeResult:
        yield self.composer

    def on_composer_message_submitted(self, event: Composer.MessageSubmitted) -> None:
        self.submitted_messages.append((event.text, event.reply_to_id, event.edit_message_id))

    def on_composer_error_reported(self, event: Composer.ErrorReported) -> None:
        self.errors.append(event.error)

    def on_composer_cancel_requested(self, event: Composer.CancelRequested) -> None:
        self.cancelled = True


@pytest.mark.asyncio
async def test_composer_send_and_empty():
    app = ComposerTestApp()
    async with app.run_test() as pilot:
        c = app.composer
        c.focus_input()
        await pilot.pause()

        # Empty enter does nothing
        await pilot.press("enter")
        assert len(app.submitted_messages) == 0

        # Type text and press enter
        c.text = "Hello world"
        await pilot.press("enter")
        assert len(app.submitted_messages) == 1
        assert app.submitted_messages[0][0] == "Hello world"
        # Input should be cleared
        assert c.text == ""


@pytest.mark.asyncio
async def test_composer_newline_fallback():
    app = ComposerTestApp()
    async with app.run_test() as pilot:
        c = app.composer
        c.focus_input()
        await pilot.pause()

        # Text ending with \ then Enter inserts newline
        c.text = "Line 1\\"
        await pilot.press("enter")
        assert len(app.submitted_messages) == 0
        assert c.text == "Line 1\n"


@pytest.mark.asyncio
async def test_composer_ctrl_j_newline():
    app = ComposerTestApp()
    async with app.run_test() as pilot:
        c = app.composer
        c.focus_input()
        await pilot.pause()

        c.text = "First"
        await pilot.press("ctrl+j")
        assert "\n" in c.text


@pytest.mark.asyncio
async def test_composer_4096_limit():
    app = ComposerTestApp()
    async with app.run_test() as pilot:
        c = app.composer
        c.focus_input()
        await pilot.pause()

        # Set text > 4096 chars
        c.text = "A" * 4097
        await pilot.press("enter")
        assert len(app.submitted_messages) == 0
        assert len(app.errors) == 1
        assert "exceeds 4096" in app.errors[0]


@pytest.mark.asyncio
async def test_composer_reply_and_edit_context():
    app = ComposerTestApp()
    async with app.run_test() as pilot:
        c = app.composer
        c.set_reply_context(message_id=42, sender_name="Bob", preview="Test")
        assert c.reply_to_id == 42
        assert c.context_bar.display is True

        c.text = "Replying now"
        await pilot.press("enter")
        assert len(app.submitted_messages) == 1
        msg, reply_id, edit_id = app.submitted_messages[0]
        assert msg == "Replying now"
        assert reply_id == 42
        assert edit_id is None
        assert c.context_bar.display is False
