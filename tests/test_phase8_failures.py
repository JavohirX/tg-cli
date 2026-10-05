"""Tests for Phase 8: Failure paths, DatabaseErrorScreen, logging, and recovery."""

from __future__ import annotations

import logging
from pathlib import Path
import pytest
from textual.app import App, ComposeResult
from tg_cli.__main__ import setup_logging
from tg_cli.app import TelegramCLIApp
from tg_cli.paths import get_app_data_dir
from tg_cli.telegram.fake import FakeGateway
from tg_cli.ui.screens.db_error import DatabaseErrorScreen


@pytest.mark.asyncio
async def test_database_error_screen():
    db_path = Path("C:/fake/appdata/tg-cli/app.db")
    err_msg = "sqlite3.OperationalError: database is locked"

    app = TelegramCLIApp(db_error=(db_path, err_msg))
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, DatabaseErrorScreen)
        assert str(db_path) in screen.db_path
        assert "database is locked" in screen.error_message

        # Press Esc to exit
        await pilot.press("escape")
        await pilot.pause()
        assert not app.is_running


@pytest.mark.asyncio
async def test_ctrl_c_double_tap_to_quit():
    gw = FakeGateway(chat_count=3)
    app = TelegramCLIApp(gateway=gw)
    async with app.run_test() as pilot:
        # First Ctrl+C: sets warning on status bar, does not quit
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app.is_running

        # Immediate second Ctrl+C: exits
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert not app.is_running


def test_setup_logging_creates_log_file(tmp_path, monkeypatch):
    monkeypatch.setattr("tg_cli.__main__.get_app_data_dir", lambda: tmp_path)
    setup_logging(debug=True)

    logger = logging.getLogger("tg_cli.test")
    logger.debug("Debug test message without secrets")

    log_file = tmp_path / "tg-cli.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "Debug test message without secrets" in content


@pytest.mark.asyncio
async def test_unavailable_chat_shows_status_line():
    from tg_cli.ui.screens.chats import ChatsScreen

    gw = FakeGateway(chat_count=1)
    acc = gw.get_accounts()[0]
    gw.accounts = [acc]
    chats = gw.get_chats(acc.user_id)
    chats[0].unavailable = True

    app = TelegramCLIApp(gateway=gw)
    async with app.run_test() as pilot:
        await pilot.pause()
        chats_screen = app.screen
        assert isinstance(chats_screen, ChatsScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert "unavailable" in chats_screen.status_bar.status_message


@pytest.mark.asyncio
async def test_messages_screen_handles_unavailable_and_status():
    from tg_cli.ui.screens.messages import MessagesScreen

    gw = FakeGateway(chat_count=1)
    acc = gw.get_accounts()[0]
    chat = gw.get_chats(acc.user_id)[0]

    def broken_get_messages(*args, **kwargs):
        raise ConnectionError("Host unreachable")

    gw.get_messages = broken_get_messages

    class ScreenHostApp(App[None]):
        def on_mount(self) -> None:
            self.push_screen(MessagesScreen(gateway=gw, account=acc, chat=chat))

    app = ScreenHostApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MessagesScreen)
        assert "Chat unavailable: Host unreachable" in screen.status_bar.status_message

        # Emit rate-limit status event
        screen._on_gateway_event("status", {"message": "rate limited 42s", "is_error": True})
        await pilot.pause()
        assert "rate limited 42s" in screen.status_bar.status_message
