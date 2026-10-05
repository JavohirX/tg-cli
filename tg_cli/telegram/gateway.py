"""Gateway protocol defining interface between UI and Telegram backends.
Screens never import Telethon; they interact only with this interface.
"""

from __future__ import annotations

from typing import Protocol, Sequence
from tg_cli.domain.models import Account, Chat, Folder, Message


class Gateway(Protocol):
    """Protocol for Telegram client communication."""

    def get_accounts(self) -> list[Account]:
        """Return list of authenticated accounts."""
        ...

    def remove_account(self, user_id: int) -> None:
        """Remove account session and credentials."""
        ...

    def get_folders(self, account_id: int) -> list[Folder]:
        """Return folders configured for account."""
        ...

    def get_chats(
        self,
        account_id: int,
        folder_id: int | None = None,
        filter_query: str = "",
    ) -> list[Chat]:
        """Return chats matching account, folder, and filter."""
        ...

    def get_chat(self, account_id: int, chat_id: int) -> Chat | None:
        """Return single chat by id."""
        ...

    def get_messages(
        self,
        account_id: int,
        chat_id: int,
        limit: int = 50,
        before_id: int | None = None,
    ) -> list[Message]:
        """Return messages for chat, oldest to newest or paged before an id."""
        ...

    def send_message(
        self,
        account_id: int,
        chat_id: int,
        text: str,
        reply_to_id: int | None = None,
    ) -> Message:
        """Send a message to a chat."""
        ...

    def edit_message(
        self,
        account_id: int,
        chat_id: int,
        message_id: int,
        text: str,
    ) -> Message:
        """Edit an existing message."""
        ...

    def delete_messages(
        self,
        account_id: int,
        chat_id: int,
        message_ids: Sequence[int],
    ) -> None:
        """Delete messages by id."""
        ...

    def archive_chats(
        self,
        account_id: int,
        chat_ids: Sequence[int],
        archived: bool = True,
    ) -> None:
        """Set archive status for chats."""
        ...

    def mute_chats(
        self,
        account_id: int,
        chat_ids: Sequence[int],
        muted: bool = True,
    ) -> None:
        """Set mute status for chats."""
        ...

    def mark_chats_read(
        self,
        account_id: int,
        chat_ids: Sequence[int],
        read: bool = True,
    ) -> None:
        """Set read status for chats."""
        ...

    def delete_chats(
        self,
        account_id: int,
        chat_ids: Sequence[int],
    ) -> None:
        """Delete chats for current user."""
        ...
