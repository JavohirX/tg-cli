"""Gateway protocol defining interface between UI and Telegram backends.
Screens never import Telethon; they interact only with this interface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence
from tg_cli.domain.models import (
    Account,
    Chat,
    Draft,
    Folder,
    Message,
    SessionInfo,
    Transcript,
    UserProfile,
)


class Gateway(Protocol):
    """Protocol for Telegram client communication."""

    def get_accounts(self) -> list[Account]:
        """Return list of authenticated accounts."""
        ...

    def get_account_unread_counts(self) -> dict[int, int]:
        """Return mapping of account_id -> total unread count."""
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
        search_query: str = "",
    ) -> list[Message]:
        """Return messages for chat, oldest to newest or paged before an id."""
        ...

    def get_message(
        self,
        account_id: int,
        chat_id: int,
        message_id: int,
    ) -> Message | None:
        """Return a single message by id."""
        ...

    def request_member_roles(self, account_id: int, chat_id: int) -> None:
        """Load admin and owner ids for a group or channel.

        The result arrives as a roles_changed event: {user_id, chat_id, roles}.
        roles maps a sender id to "admin" or "owner".
        """
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

    def restore_chats(
        self,
        account_id: int,
        chats: Sequence[Chat],
    ) -> None:
        """Restore chats after undoing deletion."""
        ...

    def get_draft(self, account_id: int, chat_id: int) -> Draft | None:
        """Retrieve stored draft for a chat."""
        ...

    def save_draft(self, draft: Draft) -> None:
        """Save draft text and context for a chat."""
        ...

    def clear_draft(self, account_id: int, chat_id: int) -> None:
        """Clear draft for a chat."""
        ...

    def retry_failed_message(self, account_id: int, chat_id: int, message_id: int) -> None:
        """Retry sending a failed message."""
        ...

    def get_transcript(self, account_id: int, chat_id: int, message_id: int) -> Transcript | None:
        """Get cached transcript for a voice message."""
        ...

    def save_transcript(self, transcript: Transcript) -> None:
        """Store transcript for a voice message."""
        ...

    def download_voice_file(self, account_id: int, chat_id: int, message_id: int) -> Path:
        """Download voice message audio to a temporary file path."""
        ...

    def get_active_sessions(self, account_id: int) -> list[SessionInfo]:
        """Return list of active authorizations/sessions for the account."""
        ...

    def revoke_session(self, account_id: int, session_hash: int) -> bool:
        """Revoke active authorization by its hash."""
        ...

    def get_user_profile(self, account_id: int) -> UserProfile:
        """Get profile details for the account user."""
        ...

    def update_user_profile(
        self,
        account_id: int,
        bio: str | None = None,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> UserProfile:
        """Update profile bio, username, or name."""
        ...

    def start_qr_login(self, api_id: int, api_hash: str) -> None:
        """Initiate QR code login token generation."""
        ...

    def cancel_qr_login(self) -> None:
        """Cancel in-progress QR code login."""
        ...

