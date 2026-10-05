"""Tests for pure domain rendering and truncation."""

from datetime import datetime, timezone
from tg_cli.domain.models import Chat, ChatKind, Message, MessageKind, SendState
from tg_cli.domain.render import (
    get_kind_glyph,
    render_chat_row,
    render_message,
    truncate_to_width,
)


def test_truncate_to_width_plain():
    text = "Hello World"
    assert truncate_to_width(text, 20) == "Hello World"
    assert truncate_to_width(text, 5) == "Hell…"


def test_truncate_to_width_cyrillic_and_emoji():
    # Emoji counts as 2 columns, Cyrillic typically 1 column each
    text = "Привет 🚀 мир!"
    truncated = truncate_to_width(text, 10)
    assert len(truncated) > 0
    # Must end with ellipsis
    assert truncated.endswith("…")


def test_render_chat_row():
    chat = Chat(
        account_id=1,
        chat_id=10,
        title="Bob 🚀",
        kind=ChatKind.USER,
        unread_count=3,
        pinned=True,
        muted=False,
        last_date=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
        last_preview="Hey there!",
    )
    rendered = render_chat_row(chat, width=80, is_cursor=True, is_selected=False)
    plain = rendered.plain
    assert "> " in plain
    assert "Bob 🚀" in plain
    assert "12:00" in plain
    assert "3" in plain


def test_render_archive_row():
    chat = Chat(
        account_id=1,
        chat_id=-1000,
        title="[Archive]",
    )
    rendered = render_chat_row(chat, width=80, is_cursor=False, is_archive_header=True)
    assert "[Archive]" in rendered.plain


def test_render_message_clamping():
    long_text = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\nLine 6"
    msg = Message(
        account_id=1,
        chat_id=10,
        message_id=1,
        sender_name="Alice",
        plain_text=long_text,
    )
    # Default is clamped to 4 body lines plus more line
    lines_clamped = render_message(msg, width=80, expanded=False)
    assert any("press 'z' to expand" in line.plain for line in lines_clamped)

    # Expanded shows all lines
    lines_expanded = render_message(msg, width=80, expanded=True)
    assert not any("press 'z' to expand" in line.plain for line in lines_expanded)


def test_render_message_kinds():
    voice_msg = Message(
        account_id=1,
        chat_id=10,
        message_id=2,
        sender_name="Bob",
        kind=MessageKind.VOICE,
        plain_text="[voice 0:45]",
    )
    lines = render_message(voice_msg, width=80, transcript="Arriving soon.")
    combined = "\n".join(l.plain for l in lines)
    assert "[voice 0:45]" in combined
    assert "transcript: Arriving soon." in combined
