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
    member_role,
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


class MessageActionPinMessage:
    pass


class MessageActionChatJoinedByLink:
    def __init__(self) -> None:
        self.inviter_id = 1


class MessageActionChatAddUser:
    def __init__(self, users: list[int]) -> None:
        self.users = users


class MessageActionChatDeleteUser:
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id


class ChannelParticipantCreator:
    pass


class ChannelParticipantAdmin:
    pass


def _service_message(action: Any, sender_id: int = 5) -> Any:
    return SimpleNamespace(
        id=7,
        sender_id=sender_id,
        sender=SimpleNamespace(first_name="Ada", last_name="", username="ada"),
        date=datetime(2026, 10, 5, 12, 30, tzinfo=timezone.utc),
        media=None,
        message="",
        action=action,
        reply_to=None,
        fwd_from=None,
        edit_date=None,
        out=False,
    )


def test_service_actions_become_sentences():
    pinned = map_telethon_message_to_domain(
        _service_message(MessageActionPinMessage()), account_id=1, chat_id=9
    )
    assert pinned.kind == MessageKind.SERVICE
    assert pinned.plain_text == "Ada pinned a message"

    joined = map_telethon_message_to_domain(
        _service_message(MessageActionChatJoinedByLink()), account_id=1, chat_id=9
    )
    assert joined.plain_text == "Ada joined the group"

    added = map_telethon_message_to_domain(
        _service_message(MessageActionChatAddUser([2, 3])), account_id=1, chat_id=9
    )
    assert added.plain_text == "Ada added 2 members"

    left = map_telethon_message_to_domain(
        _service_message(MessageActionChatDeleteUser(5), sender_id=5),
        account_id=1,
        chat_id=9,
    )
    assert left.plain_text == "Ada left the group"

    removed = map_telethon_message_to_domain(
        _service_message(MessageActionChatDeleteUser(8), sender_id=5),
        account_id=1,
        chat_id=9,
    )
    assert removed.plain_text == "Ada removed a member"


def test_service_action_fills_the_chat_preview():
    msg = _service_message(MessageActionPinMessage())
    dialog = SimpleNamespace(
        id=9,
        name="Room",
        unread_count=0,
        pinned=False,
        archived=False,
        folder_id=0,
        entity=Chat(),
        notify_settings=SimpleNamespace(mute_until=None),
        message=msg,
    )
    chat = map_telethon_dialog_to_chat(dialog, account_id=1)
    assert chat.last_preview == "Ada pinned a message"


def test_member_role_reads_creator_and_admin():
    assert member_role(ChannelParticipantCreator()) == "owner"
    assert member_role(ChannelParticipantAdmin()) == "admin"
    assert member_role(SimpleNamespace()) == ""
    assert member_role(None) == ""
