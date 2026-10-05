"""Tests for Telethon object mapping to domain models."""

from types import SimpleNamespace
from datetime import datetime, timezone
from typing import Any
from tg_cli.domain.models import AuthState, ChatKind, MessageKind
from tg_cli.telegram.map import (
    determine_chat_kind,
    determine_message_kind,
    map_telethon_dialog_to_chat,
    map_telethon_message_to_domain,
    map_telethon_user_to_account,
)


class Channel:
    def __init__(self, megagroup: bool = False) -> None:
        self.megagroup = megagroup


class Chat:
    pass


class User:
    def __init__(self, bot: bool = False) -> None:
        self.bot = bot


class DocumentAttributeAudio:
    def __init__(self, duration: int = 0) -> None:
        self.duration = duration


class MessageMediaDocument:
    def __init__(self, document: Any) -> None:
        self.document = document


def test_map_telethon_user():
    user = SimpleNamespace(
        id=123456,
        phone="+12025550199",
        username="telegram_user",
        first_name="John",
        last_name="Doe",
    )
    acc = map_telethon_user_to_account(user, session_path="session.session")
    assert acc.user_id == 123456
    assert acc.phone == "+12025550199"
    assert acc.username == "telegram_user"
    assert acc.display_name == "John Doe"
    assert acc.label == "@telegram_user"
    assert acc.auth_state == AuthState.OK


def test_determine_chat_kind():
    assert determine_chat_kind(Channel(megagroup=False)) == ChatKind.CHANNEL
    assert determine_chat_kind(Channel(megagroup=True)) == ChatKind.GROUP
    assert determine_chat_kind(Chat()) == ChatKind.GROUP
    assert determine_chat_kind(User(bot=False)) == ChatKind.USER
    assert determine_chat_kind(User(bot=True)) == ChatKind.BOT


def test_map_dialog():
    msg = SimpleNamespace(
        id=999,
        date=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
        media=None,
        message="Hello world!",
    )
    dialog = SimpleNamespace(
        id=-10012345,
        name="Team Channel",
        unread_count=5,
        pinned=True,
        archived=False,
        folder_id=0,
        entity=Channel(megagroup=False),
        notify_settings=SimpleNamespace(mute_until=None),
        message=msg,
    )

    chat = map_telethon_dialog_to_chat(dialog, account_id=101)
    assert chat.chat_id == -10012345
    assert chat.title == "Team Channel"
    assert chat.unread_count == 5
    assert chat.pinned is True
    assert chat.archived is False
    assert chat.last_message_id == 999
    assert chat.last_preview == "Hello world!"


def test_map_message_with_reply_and_voice():
    voice_attr = DocumentAttributeAudio(duration=42)
    voice_media = MessageMediaDocument(
        document=SimpleNamespace(
            mime_type="audio/ogg",
            attributes=[voice_attr],
        )
    )

    msg = SimpleNamespace(
        id=55,
        sender_id=777,
        sender=SimpleNamespace(first_name="Alice", last_name="", username="alice"),
        date=datetime(2026, 10, 5, 12, 30, tzinfo=timezone.utc),
        media=voice_media,
        message="",
        reply_to=SimpleNamespace(reply_to_msg_id=50),
        fwd_from=None,
        edit_date=None,
        out=False,
    )

    domain_msg = map_telethon_message_to_domain(msg, account_id=101, chat_id=123)
    assert domain_msg.message_id == 55
    assert domain_msg.sender_name == "Alice"
    assert domain_msg.kind == MessageKind.VOICE
    assert "[0:42]" in domain_msg.plain_text
    assert domain_msg.reply_id == 50
    assert domain_msg.outgoing is False
