"""Tests for Phase 5: Bulk actions, confirmation prompts, and 5-second undo."""

from __future__ import annotations

import time
import pytest
from textual.app import App, ComposeResult
from tg_cli.domain.models import Account, Chat, ChatKind
from tg_cli.store.db import init_db
from tg_cli.store.repo import get_chats, upsert_account, upsert_chats
from tg_cli.telegram.fake import FakeGateway
from tg_cli.ui.screens.chats import ARCHIVE_CHAT_ID, ChatsScreen


class ChatsPilotApp(App):
    def __init__(self, gateway: FakeGateway, account: Account) -> None:
        super().__init__()
        self.gateway = gateway
        self.account = account

    def compose(self) -> ComposeResult:
        return []

    def on_mount(self) -> None:
        self.push_screen(
            ChatsScreen(gateway=self.gateway, account=self.account)
        )


@pytest.mark.asyncio
async def test_bulk_delete_four_chats_and_undo():
    gw = FakeGateway(chat_count=15)
    acc = gw.get_accounts()[0]
    initial_chats = gw.get_chats(acc.user_id)
    assert len(initial_chats) >= 10

    app = ChatsPilotApp(gw, acc)
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, ChatsScreen)

        # Skip archive row (index 0) and select 4 chats (indexes 1, 2, 3, 4) using space
        screen.chat_list.cursor = 1
        await pilot.press("space")  # select chat 1
        await pilot.press("j")
        await pilot.press("space")  # select chat 2
        await pilot.press("j")
        await pilot.press("space")  # select chat 3
        await pilot.press("j")
        await pilot.press("space")  # select chat 4

        selected_ids = list(screen.chat_list.selected_ids)
        assert len(selected_ids) == 4

        # Press 'd' to delete
        await pilot.press("d")
        await pilot.pause()

        # Selection should be cleared and chats removed from gateway
        assert len(screen.chat_list.selected_ids) == 0
        remaining_ids = {c.chat_id for c in gw.get_chats(acc.user_id)}
        for cid in selected_ids:
            assert cid not in remaining_ids

        # Status toast should offer undo
        toast_text = screen.status_bar.toast_message or ""
        assert "Deleted 4 chat(s)" in toast_text
        assert "undo" in toast_text

        # Press 'u' within 5 seconds to undo
        await pilot.press("u")
        await pilot.pause()

        # Chats should be restored in gateway and on screen
        restored_ids = {c.chat_id for c in gw.get_chats(acc.user_id)}
        for cid in selected_ids:
            assert cid in restored_ids


@pytest.mark.asyncio
async def test_delete_more_than_ten_chats_requires_confirmation():
    gw = FakeGateway(chat_count=20)
    acc = gw.get_accounts()[0]

    app = ChatsPilotApp(gw, acc)
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, ChatsScreen)

        # Select 11 chats using visual mode ('v' at index 1, then navigate down 10 times)
        screen.chat_list.cursor = 1
        await pilot.press("v")
        for _ in range(10):
            await pilot.press("j")

        assert len(screen.chat_list.selected_ids) == 11
        selected_ids = list(screen.chat_list.selected_ids)

        # Press 'd' to trigger delete
        await pilot.press("d")
        await pilot.pause()

        # Should NOT delete immediately; status bar shows prompt
        prompt = screen.status_bar.prompt_message or ""
        assert "Delete 11 chats for me? (y/n)" in prompt
        # Chats still exist in gateway
        current_ids = {c.chat_id for c in gw.get_chats(acc.user_id)}
        assert all(cid in current_ids for cid in selected_ids)

        # Press 'n' to cancel
        await pilot.press("n")
        await pilot.pause()
        assert screen._pending_confirm is None
        assert "Action cancelled" in (screen.status_bar.status_message or "")
        assert all(cid in {c.chat_id for c in gw.get_chats(acc.user_id)} for cid in selected_ids)

        # Now trigger again and confirm with 'y'
        await pilot.press("d")
        await pilot.pause()
        assert screen._pending_confirm is not None

        await pilot.press("y")
        await pilot.pause()

        # Now chats should be deleted
        remaining_ids = {c.chat_id for c in gw.get_chats(acc.user_id)}
        for cid in selected_ids:
            assert cid not in remaining_ids


@pytest.mark.asyncio
async def test_bulk_archive_mute_and_read():
    gw = FakeGateway(chat_count=10)
    acc = gw.get_accounts()[0]

    app = ChatsPilotApp(gw, acc)
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, ChatsScreen)

        # Focus first real chat
        screen.chat_list.cursor = 1
        chat1 = screen.chat_list.items[1]
        cid = chat1.chat_id

        # Mute toggle 'm'
        was_muted = chat1.muted
        await pilot.press("m")
        await pilot.pause()
        assert gw.chats[acc.user_id][cid].muted != was_muted

        # Undo mute 'u'
        await pilot.press("u")
        await pilot.pause()
        assert gw.chats[acc.user_id][cid].muted == was_muted

        # Mark read 'r' and unread 'R'
        await pilot.press("r")
        await pilot.pause()
        assert gw.chats[acc.user_id][cid].unread_count == 0

        await pilot.press("R")
        await pilot.pause()
        assert gw.chats[acc.user_id][cid].unread_count > 0


def test_muted_archived_chat_survives_restart(tmp_path):
    db_file = tmp_path / "test_restart.db"
    conn = init_db(db_file)

    acc = Account(user_id=42, phone="+1234567890", display_name="TestUser")
    upsert_account(conn, acc)

    chat = Chat(
        account_id=42,
        chat_id=1001,
        title="Secret Muted Archive Chat",
        kind=ChatKind.GROUP,
        muted=True,
        archived=True,
        unread_count=5,
    )
    upsert_chats(conn, [chat])
    conn.close()

    # Reopen connection (simulating app restart)
    conn2 = init_db(db_file)
    restored = get_chats(conn2, account_id=42, folder_id=-2)  # -2 is archived
    conn2.close()

    assert len(restored) == 1
    c = restored[0]
    assert c.chat_id == 1001
    assert c.title == "Secret Muted Archive Chat"
    assert c.muted is True
    assert c.archived is True
    assert c.unread_count == 5
