"""Tests for Phase 3: Message streaming, date/unread separators, history pagination, and live updates."""

import pytest
from datetime import datetime, timedelta, timezone
from tg_cli.app import TelegramCLIApp
from tg_cli.domain.models import (
    Account,
    AuthState,
    Chat,
    ChatKind,
    DateSeparator,
    Message,
    MessageKind,
    UnreadDivider,
)
from tg_cli.telegram.fake import FakeGateway
from tg_cli.ui.screens.messages import MessagesScreen


class MessagePagingGateway(FakeGateway):
    def __init__(self):
        super().__init__(chat_count=5)
        self.listeners = []
        now = datetime.now(timezone.utc)
        # Create 120 messages spanning 3 different days
        self.messages[(1001, 1)] = [
            Message(
                account_id=1001,
                chat_id=1,
                message_id=i,
                sender_id=1001 if i % 2 == 0 else 2002,
                sender_name="Alice" if i % 2 == 0 else "Bob",
                date=now - timedelta(days=2 if i < 40 else (1 if i < 80 else 0), minutes=120 - i),
                kind=MessageKind.TEXT,
                plain_text=f"Message #{i}",
                outgoing=(i % 2 == 0),
            )
            for i in range(1, 121)
        ]

    def register_listener(self, listener):
        self.listeners.append(listener)

    def unregister_listener(self, listener):
        if listener in self.listeners:
            self.listeners.remove(listener)

    def emit_event(self, event_type: str, data: dict):
        for listener in self.listeners:
            listener(event_type, data)


@pytest.mark.asyncio
async def test_messages_separators_and_skipping():
    gw = MessagePagingGateway()
    alice = gw.get_accounts()[0]
    chat = gw.get_chats(alice.user_id)[0]
    chat.unread_count = 5  # Has 5 unread messages

    screen = MessagesScreen(gateway=gw, account=alice, chat=chat)
    app = TelegramCLIApp(gateway=gw)

    async with app.run_test() as pilot:
        await app.push_screen(screen)
        await pilot.pause()

        # Check items contain DateSeparators and UnreadDivider
        items = screen.message_list.items
        assert any(isinstance(it, DateSeparator) for it in items)
        assert any(isinstance(it, UnreadDivider) for it in items)

        # Cursor should be pointing to a Message, NOT a separator or divider
        focused = screen.message_list.get_focused_item()
        assert isinstance(focused, Message)


@pytest.mark.asyncio
async def test_messages_history_paging_up():
    gw = MessagePagingGateway()
    alice = gw.get_accounts()[0]
    chat = gw.get_chats(alice.user_id)[0]

    screen = MessagesScreen(gateway=gw, account=alice, chat=chat)
    app = TelegramCLIApp(gateway=gw)

    async with app.run_test() as pilot:
        await app.push_screen(screen)
        await pilot.pause()

        initial_count = len(screen._all_messages)
        assert initial_count == 50

        # Jump to top
        await pilot.press("home")
        await pilot.pause()
        focused_before = screen.message_list.get_focused_item()
        assert focused_before is not None

        # Press 'k' at the top to load older messages
        await pilot.press("k")
        await pilot.pause()

        # Older messages should be prepended
        new_count = len(screen._all_messages)
        assert new_count > initial_count

        # Cursor should stay on the exact same message without jumping!
        focused_after = screen.message_list.get_focused_item()
        assert focused_after.message_id == focused_before.message_id


@pytest.mark.asyncio
async def test_messages_live_badge_when_scrolled_up():
    gw = MessagePagingGateway()
    alice = gw.get_accounts()[0]
    chat = gw.get_chats(alice.user_id)[0]

    screen = MessagesScreen(gateway=gw, account=alice, chat=chat)
    app = TelegramCLIApp(gateway=gw)

    async with app.run_test() as pilot:
        await app.push_screen(screen)
        await pilot.pause()

        # Scroll up so cursor is away from the bottom
        await pilot.press("ctrl+u", "ctrl+u")
        await pilot.pause()

        # Simulate new message arriving from someone else
        now = datetime.now(timezone.utc)
        new_msg = Message(
            account_id=alice.user_id,
            chat_id=chat.chat_id,
            message_id=999,
            sender_id=2002,
            sender_name="Bob",
            date=now,
            plain_text="Live arrival",
        )
        gw.messages[(alice.user_id, chat.chat_id)].append(new_msg)
        gw.emit_event("messages_changed", {"chat_id": chat.chat_id})
        await pilot.pause()

        # Should show unseen badge in header/status
        assert screen._unseen_count == 1
        assert "↓ 1 new" in screen.status_bar.status_message

        # Press End to jump to newest
        await pilot.press("end")
        await pilot.pause()

        # Unseen count reset and chat marked read
        assert screen._unseen_count == 0
        focused = screen.message_list.get_focused_item()
        assert focused.message_id == 999
