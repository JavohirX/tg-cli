"""Tests for Phase 2: Chat list from Telegram, sync, and folder/filter interactions."""

import pytest
from datetime import datetime, timedelta, timezone
from tg_cli.app import TelegramCLIApp
from tg_cli.domain.models import Account, AuthState, Chat, ChatKind, Folder
from tg_cli.telegram.fake import FakeGateway
from tg_cli.ui.screens.chats import ARCHIVE_CHAT_ID, ChatsScreen


class Phase2Gateway(FakeGateway):
    """Gateway that tracks dynamic event subscriptions."""

    def __init__(self):
        super().__init__(chat_count=20)
        self.listeners = []

    def register_listener(self, listener):
        self.listeners.append(listener)

    def unregister_listener(self, listener):
        if listener in self.listeners:
            self.listeners.remove(listener)

    def emit_event(self, event_type: str, data: dict):
        for listener in self.listeners:
            listener(event_type, data)


@pytest.mark.asyncio
async def test_phase2_chat_sync_and_interactions():
    gw = Phase2Gateway()
    alice = gw.get_accounts()[0]
    app = TelegramCLIApp(gateway=gw)

    async with app.run_test() as pilot:
        # Open Alice's chats
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ChatsScreen)
        screen: ChatsScreen = app.screen

        # 1. Cursor starts on the first real chat (index 1), skipping [Archive] at index 0
        assert screen.chat_list.cursor == 1
        assert screen.chat_list.items[0].chat_id == ARCHIVE_CHAT_ID

        # 2. Moving down
        await pilot.press("j")
        assert screen.chat_list.cursor == 2

        # 3. Simulate background chats_changed event arriving from sync
        # Add a new chat to the gateway
        now = datetime.now(timezone.utc)
        new_chat = Chat(
            account_id=alice.user_id,
            chat_id=9999,
            title="Newly Synced Group",
            kind=ChatKind.GROUP,
            unread_count=1,
            last_date=now,
            last_preview="New message",
        )
        gw.chats[alice.user_id][9999] = new_chat
        gw.emit_event("chats_changed", {"user_id": alice.user_id})
        await pilot.pause()

        # The new chat should appear in the items list, and cursor should NOT be reset to 0
        assert any(c.chat_id == 9999 for c in screen.chat_list.items)
        assert screen.chat_list.cursor == 2

        # 4. Folder switching clears selection
        await pilot.press("space")  # select current item
        assert len(screen.chat_list.selected_ids) == 1

        # Press ']' to switch folder -> selection must be cleared
        await pilot.press("]")
        assert len(screen.chat_list.selected_ids) == 0

        # 5. Filter query narrows the list and clears selection
        await pilot.press("[")  # back to All
        await pilot.press("space")
        assert len(screen.chat_list.selected_ids) == 1

        await pilot.press("/")
        await pilot.press("n", "e", "w")
        assert len(screen.chat_list.selected_ids) == 0
        assert all("new" in c.title.lower() or "new" in c.last_preview.lower() for c in screen.chat_list.items if c.chat_id != ARCHIVE_CHAT_ID)
