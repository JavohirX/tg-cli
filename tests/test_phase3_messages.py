"""Tests for Phase 3: Message streaming, date/unread separators, history pagination, and live updates."""

import io

import pytest
from datetime import datetime, timedelta, timezone
from rich.console import Console, Group
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
from tg_cli.domain.render import format_time
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


def _plain_lines(renderable) -> list[str]:
    buf = io.StringIO()
    console = Console(file=buf, width=80, force_terminal=False)
    console.print(renderable)
    return buf.getvalue().splitlines()


def test_one_blank_line_between_messages_in_every_chat():
    """People, bots, groups, and channels get a single blank line between messages."""
    account = Account(user_id=1, phone="+100")
    for kind in (ChatKind.USER, ChatKind.BOT, ChatKind.GROUP, ChatKind.CHANNEL):
        chat = Chat(account_id=1, chat_id=1, title="Chat", kind=kind)
        screen = MessagesScreen(gateway=object(), account=account, chat=chat)
        first = Message(
            account_id=1,
            chat_id=1,
            message_id=1,
            sender_id=7,
            sender_name="Ada",
            plain_text="hello",
        )
        second = Message(
            account_id=1,
            chat_id=1,
            message_id=2,
            sender_id=8,
            sender_name="Bea",
            plain_text="there",
            outgoing=True,
        )
        screen._build_stream_items([first, second])
        rendered = [screen._render_row_item(msg, 80, False, False) for msg in (first, second)]
        first_lines = _plain_lines(rendered[0])
        second_lines = _plain_lines(rendered[1])
        joined = _plain_lines(Group(*rendered))

        assert first_lines[-2] != ""
        assert second_lines[0] != ""
        assert joined == first_lines + second_lines
        assert joined[len(first_lines) - 1] == ""
        height_width = max(10, getattr(screen.message_list.size, "width", 80))
        painted = _plain_lines(screen._render_row_item(first, height_width, False, False))
        assert screen._get_item_height(first) == len(painted)

        text = "\n".join(joined)
        if kind in (ChatKind.USER, ChatKind.BOT):
            assert "Ada" not in text
            assert "Bea" not in text
        else:
            assert "Ada" in first_lines[0]


def test_same_author_burst_is_one_bundle():
    """Messages within a minute of the first share its clock and have no gap."""
    account = Account(user_id=1, phone="+100")
    base = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    for kind in (ChatKind.USER, ChatKind.BOT, ChatKind.GROUP):
        chat = Chat(account_id=1, chat_id=1, title="Chat", kind=kind)
        screen = MessagesScreen(gateway=object(), account=account, chat=chat)
        messages = [
            Message(
                account_id=1, chat_id=1, message_id=1, sender_id=7, sender_name="Ada",
                date=base, plain_text="one",
            ),
            Message(
                account_id=1, chat_id=1, message_id=2, sender_id=7, sender_name="Ada",
                date=base + timedelta(seconds=40), plain_text="two",
            ),
            Message(
                account_id=1, chat_id=1, message_id=3, sender_id=7, sender_name="Ada",
                date=base + timedelta(minutes=3), plain_text="three",
            ),
        ]
        screen._build_stream_items(messages)
        lines = _plain_lines(Group(*(
            screen._render_row_item(msg, 80, False, False) for msg in messages
        )))
        one_i = next(i for i, line in enumerate(lines) if line.endswith("one"))
        two_i = next(i for i, line in enumerate(lines) if line.endswith("two"))
        three_i = next(i for i, line in enumerate(lines) if line.endswith("three"))
        opened = format_time(base)
        assert opened in (lines[one_i] if kind in (ChatKind.USER, ChatKind.BOT) else lines[one_i - 1])
        assert opened not in lines[two_i]
        assert "" not in lines[one_i:two_i]
        assert "" in lines[two_i:three_i]
        assert format_time(messages[2].date) in "\n".join(lines[two_i + 1:three_i + 1])
        if kind in (ChatKind.USER, ChatKind.BOT):
            assert "Ada" not in "\n".join(lines)


@pytest.mark.asyncio
async def test_z_expands_the_focused_message():
    gw = FakeGateway()
    alice = gw.get_accounts()[0]
    chat = gw.get_chats(alice.user_id)[0]
    long_text = "\n".join(f"Line {i} of the long message" for i in range(1, 30))
    gw.messages[(alice.user_id, chat.chat_id)] = [
        Message(
            account_id=alice.user_id,
            chat_id=chat.chat_id,
            message_id=7,
            sender_id=alice.user_id,
            sender_name="Ada",
            date=datetime.now(timezone.utc),
            kind=MessageKind.TEXT,
            plain_text=long_text,
        )
    ]
    screen = MessagesScreen(gateway=gw, account=alice, chat=chat)
    app = TelegramCLIApp(gateway=gw)

    async with app.run_test(size=(100, 24)) as pilot:
        await app.push_screen(screen)
        await pilot.pause()
        before = _plain_lines(screen.message_list.render())
        assert any("press 'z' to expand" in line for line in before)
        assert not any("Line 20 of the long message" in line for line in before)

        await pilot.press("z")
        await pilot.pause()

        def visible_lines() -> list[str]:
            widget = screen.message_list
            rows = []
            for y in range(widget.size.height):
                rows.append("".join(seg.text for seg in widget.render_line(y)))
            return rows

        after = _plain_lines(screen.message_list.render())
        shown = visible_lines()
        assert 7 in screen.expanded_message_ids
        assert any("Line 20 of the long message" in line for line in after)
        assert not any("press 'z' to expand" in line for line in after)
        assert any("Line 13 of the long message" in line for line in shown), shown
        assert not any("press 'z' to expand" in line for line in shown), shown


def _widget_lines(widget) -> list[str]:
    rows = []
    for y in range(widget.size.height):
        rows.append("".join(seg.text for seg in widget.render_line(y)))
    return rows


def _long_message(account_id: int, chat_id: int, message_id: int, text: str, when: datetime) -> Message:
    return Message(
        account_id=account_id,
        chat_id=chat_id,
        message_id=message_id,
        sender_id=account_id,
        sender_name="Ada",
        date=when,
        kind=MessageKind.TEXT,
        plain_text=text,
    )


@pytest.mark.asyncio
async def test_shift_z_expands_the_focused_message():
    gw = FakeGateway()
    alice = gw.get_accounts()[0]
    chat = gw.get_chats(alice.user_id)[0]
    long_text = "\n".join(f"Line {i} of the long message" for i in range(1, 30))
    gw.messages[(alice.user_id, chat.chat_id)] = [
        _long_message(alice.user_id, chat.chat_id, 7, long_text, datetime.now(timezone.utc))
    ]
    screen = MessagesScreen(gateway=gw, account=alice, chat=chat)
    app = TelegramCLIApp(gateway=gw)

    async with app.run_test(size=(100, 24)) as pilot:
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("Z")
        await pilot.pause()
        assert 7 in screen.expanded_message_ids
        assert any("Line 13 of the long message" in line for line in _widget_lines(screen.message_list))


@pytest.mark.asyncio
async def test_z_expands_when_composer_is_focused_but_not_writing():
    """Clicking the composer used to swallow z without entering write mode."""
    gw = FakeGateway()
    alice = gw.get_accounts()[0]
    chat = gw.get_chats(alice.user_id)[0]
    long_text = "\n".join(f"Line {i} of the long message" for i in range(1, 30))
    gw.messages[(alice.user_id, chat.chat_id)] = [
        _long_message(alice.user_id, chat.chat_id, 7, long_text, datetime.now(timezone.utc))
    ]
    screen = MessagesScreen(gateway=gw, account=alice, chat=chat)
    app = TelegramCLIApp(gateway=gw)

    async with app.run_test(size=(100, 24)) as pilot:
        await app.push_screen(screen)
        await pilot.pause()
        screen.composer.focus_input()
        await pilot.pause()
        assert screen.is_write_mode is False

        await pilot.press("z")
        await pilot.pause()

        assert 7 in screen.expanded_message_ids
        assert screen.composer.text == ""
        assert any("Line 13 of the long message" in line for line in _widget_lines(screen.message_list))


@pytest.mark.asyncio
async def test_z_types_into_the_composer_in_write_mode():
    gw = FakeGateway()
    alice = gw.get_accounts()[0]
    chat = gw.get_chats(alice.user_id)[0]
    long_text = "\n".join(f"Line {i} of the long message" for i in range(1, 30))
    gw.messages[(alice.user_id, chat.chat_id)] = [
        _long_message(alice.user_id, chat.chat_id, 7, long_text, datetime.now(timezone.utc))
    ]
    screen = MessagesScreen(gateway=gw, account=alice, chat=chat)
    app = TelegramCLIApp(gateway=gw)

    async with app.run_test(size=(100, 24)) as pilot:
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert screen.is_write_mode is True

        await pilot.press("z")
        await pilot.pause()

        assert screen.expanded_message_ids == set()
        assert screen.composer.text == "z"


@pytest.mark.asyncio
async def test_z_expands_the_visible_hint_when_the_cursor_is_on_a_short_message():
    gw = FakeGateway()
    alice = gw.get_accounts()[0]
    chat = gw.get_chats(alice.user_id)[0]
    now = datetime.now(timezone.utc)
    long_text = "\n".join(f"Line {i} of the long message" for i in range(1, 30))
    gw.messages[(alice.user_id, chat.chat_id)] = [
        _long_message(alice.user_id, chat.chat_id, 7, long_text, now),
        _long_message(alice.user_id, chat.chat_id, 8, "ok", now + timedelta(minutes=5)),
    ]
    screen = MessagesScreen(gateway=gw, account=alice, chat=chat)
    app = TelegramCLIApp(gateway=gw)

    async with app.run_test(size=(100, 40)) as pilot:
        await app.push_screen(screen)
        await pilot.pause()
        focused = screen.message_list.get_focused_item()
        assert isinstance(focused, Message)
        assert focused.message_id == 8
        assert any("press 'z' to expand" in line for line in _widget_lines(screen.message_list))

        await pilot.press("z")
        await pilot.pause()

        shown = _widget_lines(screen.message_list)
        assert 7 in screen.expanded_message_ids
        assert 8 not in screen.expanded_message_ids
        assert any("Line 13 of the long message" in line for line in shown), shown
        assert not any("press 'z' to expand" in line for line in shown), shown


@pytest.mark.asyncio
async def test_z_reveals_hidden_lines_when_the_list_is_shorter_than_the_message():
    gw = FakeGateway()
    alice = gw.get_accounts()[0]
    chat = gw.get_chats(alice.user_id)[0]
    long_text = "\n".join(f"Line {i} of the long message" for i in range(1, 30))
    gw.messages[(alice.user_id, chat.chat_id)] = [
        _long_message(alice.user_id, chat.chat_id, 7, long_text, datetime.now(timezone.utc))
    ]
    screen = MessagesScreen(gateway=gw, account=alice, chat=chat)
    app = TelegramCLIApp(gateway=gw)

    async with app.run_test(size=(80, 16)) as pilot:
        await app.push_screen(screen)
        await pilot.pause()
        assert screen.message_list.size.height < 14
        assert not any("Line 13 of the long message" in line for line in _widget_lines(screen.message_list))

        await pilot.press("z")
        await pilot.pause()

        shown = _widget_lines(screen.message_list)
        assert 7 in screen.expanded_message_ids
        assert any("Line 13 of the long message" in line for line in shown), shown


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


@pytest.mark.asyncio
async def test_messages_opens_at_latest_message_and_mouse_click():
    """Verify entering a chat opens directly at the latest message at the bottom, and clicking selects rows."""
    gw = MessagePagingGateway()
    alice = gw.get_accounts()[0]
    chat = gw.get_chats(alice.user_id)[0]

    screen = MessagesScreen(gateway=gw, account=alice, chat=chat)
    app = TelegramCLIApp(gateway=gw)

    async with app.run_test() as pilot:
        await app.push_screen(screen)
        await pilot.pause()

        # The focused item upon opening MUST be the latest message (message #120)
        focused = screen.message_list.get_focused_item()
        assert focused is not None
        assert isinstance(focused, Message)
        assert focused.message_id == 120, f"Expected latest message #120, got #{focused.message_id}"

        # Press 'k' moves up to previous message
        await pilot.press("k")
        await pilot.pause()
        focused_up = screen.message_list.get_focused_item()
        assert focused_up.message_id == 119

        # Press 'j' moves down to message 120
        await pilot.press("j")
        await pilot.pause()
        focused_down = screen.message_list.get_focused_item()
        assert focused_down.message_id == 120

        # Simulate clicking on the message list: switches write mode off if active and sets focus
        screen._set_focus_mode(is_write=True)
        assert screen.is_write_mode is True

        # Click inside message_list
        from textual.events import Click
        screen.message_list.on_click(Click(screen.message_list, 10, 2, 10, 2, 10, 2, 0, False, False, False))
        await pilot.pause()

        assert screen.is_write_mode is False
        assert screen.message_list.has_focus is True


def test_group_screen_prints_admin_and_owner_after_the_name():
    account = Account(user_id=1, phone="+100")
    chat = Chat(account_id=1, chat_id=1, title="Room", kind=ChatKind.GROUP)
    screen = MessagesScreen(gateway=object(), account=account, chat=chat)
    screen._member_roles = {7: "admin", 8: "owner"}
    admin = Message(
        account_id=1,
        chat_id=1,
        message_id=1,
        sender_id=7,
        sender_name="Ada",
        plain_text="hello",
    )
    owner = Message(
        account_id=1,
        chat_id=1,
        message_id=2,
        sender_id=8,
        sender_name="Bea",
        plain_text="hi",
    )
    screen._build_stream_items([admin, owner])
    admin_lines = _plain_lines(screen._render_row_item(admin, 80, False, False))
    owner_lines = _plain_lines(screen._render_row_item(owner, 80, False, False))
    assert any("Ada admin" in line for line in admin_lines)
    assert any("Bea owner" in line for line in owner_lines)

