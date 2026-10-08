"""Domain models for tg-cli.
Pure data structures representing accounts, chats, messages, and state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal


class AuthState(str, Enum):
    OK = "ok"
    EXPIRED = "expired"
    NEEDS_2FA = "needs_2fa"


class ChatKind(str, Enum):
    USER = "user"
    GROUP = "group"
    CHANNEL = "channel"
    BOT = "bot"


class MessageKind(str, Enum):
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    VOICE = "voice"
    FILE = "file"
    STICKER = "sticker"
    GIF = "gif"
    LOCATION = "location"
    CONTACT = "contact"
    POLL = "poll"
    OTHER = "other"
    SERVICE = "service"


class SendState(str, Enum):
    SENT = "sent"
    QUEUED = "queued"
    FAILED = "failed"


@dataclass(slots=True)
class Account:
    user_id: int
    phone: str
    username: str = ""
    display_name: str = ""
    session_path: str = ""
    auth_state: AuthState = AuthState.OK

    @property
    def label(self) -> str:
        if self.username:
            return f"@{self.username}"
        if self.display_name:
            return self.display_name
        return self.phone or str(self.user_id)


@dataclass(slots=True)
class Folder:
    account_id: int
    folder_id: int
    title: str
    position: int = 0


@dataclass(slots=True)
class Chat:
    account_id: int
    chat_id: int
    title: str
    kind: ChatKind = ChatKind.USER
    unread_count: int = 0
    muted: bool = False
    pinned: bool = False
    pin_rank: int = 0
    archived: bool = False
    last_message_id: int = 0
    last_date: datetime | None = None
    last_preview: str = ""
    folder_ids: set[int] = field(default_factory=set)
    unavailable: bool = False

    @property
    def identity(self) -> tuple[int, int]:
        return (self.account_id, self.chat_id)


@dataclass(slots=True)
class Message:
    account_id: int
    chat_id: int
    message_id: int
    sender_id: int = 0
    sender_name: str = ""
    date: datetime | None = None
    kind: MessageKind = MessageKind.TEXT
    plain_text: str = ""
    caption: str = ""
    entity_spans: list[dict[str, Any]] = field(default_factory=list)
    reply_id: int | None = None
    forward_label: str = ""
    edited: bool = False
    outgoing: bool = False
    send_state: SendState = SendState.SENT
    is_deleted: bool = False

    @property
    def identity(self) -> tuple[int, int, int]:
        return (self.account_id, self.chat_id, self.message_id)


@dataclass(slots=True)
class Draft:
    account_id: int
    chat_id: int
    text: str
    reply_to_id: int | None = None
    edit_message_id: int | None = None


@dataclass(slots=True)
class Transcript:
    account_id: int
    chat_id: int
    message_id: int
    text: str = ""
    model: str = ""
    error: str = ""
    created_at: datetime | None = None


@dataclass(slots=True)
class HistoryWindow:
    account_id: int
    chat_id: int
    oldest_loaded_id: int
    newest_loaded_id: int


@dataclass(slots=True)
class DateSeparator:
    date_str: str

    @property
    def identity(self) -> str:
        return f"date_sep_{self.date_str}"


@dataclass(slots=True)
class UnreadDivider:
    @property
    def identity(self) -> str:
        return "unread_divider"


@dataclass(slots=True)
class SessionInfo:
    hash: int
    device_model: str
    platform: str
    system_version: str
    ip: str
    country: str
    date_active: datetime | None = None
    date_created: datetime | None = None
    is_current: bool = False
    app_name: str = ""
    app_version: str = ""

    @property
    def title(self) -> str:
        parts = [p for p in (self.device_model, self.platform) if p]
        return " · ".join(parts) or "Unknown Device"


@dataclass(slots=True)
class UserProfile:
    user_id: int
    first_name: str
    last_name: str = ""
    username: str = ""
    phone: str = ""
    bio: str = ""
    status_emoji: str = ""
    is_online: bool = True


