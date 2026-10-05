"""Tests for Phase 6: Search, Command Palette, Contextual Help, and First-visit banner."""

from __future__ import annotations

import json
import pytest
from textual.app import App, ComposeResult
from tg_cli.commands import COMMANDS, Command, get_commands_for_context
from tg_cli.config import has_seen_help, mark_help_seen
from tg_cli.domain.models import Account, Chat, ChatKind, Message, MessageKind
from tg_cli.paths import get_config_path
from tg_cli.telegram.fake import FakeGateway
from tg_cli.ui.screens.chats import ChatsScreen
from tg_cli.ui.screens.help import HelpScreen
from tg_cli.ui.screens.messages import MessagesScreen
from tg_cli.ui.screens.palette import CommandPalette


class PilotApp(App):
    def __init__(self, screen_factory) -> None:
        super().__init__()
        self.screen_factory = screen_factory

    def compose(self) -> ComposeResult:
        return []

    def on_mount(self) -> None:
        self.push_screen(self.screen_factory())


def test_every_command_appears_in_palette_for_its_context():
    for ctx in ("accounts", "chats", "messages"):
        palette = CommandPalette(context=ctx)
        palette_cmd_ids = {cmd.id for cmd in palette.available_commands}

        expected_cmds = [
            cmd for cmd in COMMANDS
            if ctx in cmd.contexts or "global" in cmd.contexts
        ]
        expected_ids = {cmd.id for cmd in expected_cmds}

        assert palette_cmd_ids == expected_ids, f"Mismatch in context {ctx}"


@pytest.mark.asyncio
async def test_destructive_palette_command_asks_confirmation():
    gw = FakeGateway(chat_count=5)
    acc = gw.get_accounts()[0]

    app = PilotApp(lambda: ChatsScreen(gateway=gw, account=acc))
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, ChatsScreen)
        screen.chat_list.cursor = 1
        focused_chat = screen.chat_list.items[1]
        cid = focused_chat.chat_id

        # Open palette
        await pilot.press("ctrl+k")
        await pilot.pause()

        palette = app.screen
        assert isinstance(palette, CommandPalette)

        # Filter for "delete"
        palette.query_one("#palette-input").value = "delete"
        await pilot.pause()

        # Select option
        await pilot.press("enter")
        await pilot.pause()

        # Back on ChatsScreen, should see confirmation prompt
        chats_screen = app.screen
        assert isinstance(chats_screen, ChatsScreen)
        assert chats_screen._pending_confirm is not None
        assert "Delete Chat" in (chats_screen.status_bar.prompt_message or "")

        # Chat is NOT deleted yet
        assert cid in gw.chats[acc.user_id]

        # Confirm with 'y'
        await pilot.press("y")
        await pilot.pause()

        # Chat is now deleted
        assert cid not in gw.chats[acc.user_id]


def test_first_visit_help_flow(tmp_path, monkeypatch):
    # Route config to temp dir
    fake_config = tmp_path / "config.json"
    monkeypatch.setattr("tg_cli.config.get_config_path", lambda: fake_config)

    assert not has_seen_help()

    # Mark help seen
    mark_help_seen()
    assert has_seen_help()


@pytest.mark.asyncio
async def test_first_visit_banner_on_chats_screen(tmp_path, monkeypatch):
    fake_config = tmp_path / "config2.json"
    monkeypatch.setattr("tg_cli.config.get_config_path", lambda: fake_config)

    gw = FakeGateway(chat_count=3)
    acc = gw.get_accounts()[0]

    app = PilotApp(lambda: ChatsScreen(gateway=gw, account=acc))
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, ChatsScreen)
        # First visit: status shows "Press ? for keys"
        assert "Press ? for keys" in (screen.status_bar.status_message or "")

        # Open help screen
        await pilot.press("?")
        await pilot.pause()

        help_screen = app.screen
        assert isinstance(help_screen, HelpScreen)
        # Help has now been marked seen
        assert has_seen_help()


@pytest.mark.asyncio
async def test_in_chat_search_and_query_gateway():
    gw = FakeGateway(chat_count=3)
    acc = gw.get_accounts()[0]
    chat = gw.get_chats(acc.user_id)[0]

    # Pre-populate history with an older message not in the initial 10-message window
    secret_msg = Message(
        account_id=acc.user_id,
        chat_id=chat.chat_id,
        message_id=999,
        sender_id=acc.user_id,
        sender_name="Alice",
        plain_text="Very special pineapple secret code",
        outgoing=False,
    )
    # Put it deep in gateway history
    gw.messages[(acc.user_id, chat.chat_id)].insert(0, secret_msg)

    app = PilotApp(lambda: MessagesScreen(gateway=gw, account=acc, chat=chat))
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, MessagesScreen)

        # Press '/' to start search
        await pilot.press("/")
        await pilot.pause()

        filter_container = screen.query_one("#filter-container")
        assert filter_container.display is True

        # Type query and submit with enter
        screen.filter_input.value = "pineapple"
        await pilot.press("enter")
        await pilot.pause()

        # The message should have been fetched from gateway and displayed
        displayed_texts = [
            m.plain_text for m in screen.message_list.items if isinstance(m, Message)
        ]
        assert any("pineapple" in t for t in displayed_texts)

        # Press Esc to clear search
        await pilot.press("escape")
        await pilot.pause()

        assert filter_container.display is False
        assert screen.search_query == ""
