"""Telethon Gateway implementing the Gateway protocol.
This is the only module allowed to import Telethon and connect the UI to the background worker.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Sequence
from tg_cli.config import load_config
from tg_cli.domain.models import (
    Account,
    AuthState,
    Chat,
    Draft,
    Folder,
    Message,
    MessageKind,
    SendState,
)
from tg_cli.paths import get_session_path
from tg_cli.store.db import init_db
from tg_cli.store.repo import (
    archive_chats as repo_archive_chats,
    clear_draft as repo_clear_draft,
    delete_chats as repo_delete_chats,
    delete_messages as repo_delete_messages,
    get_accounts as repo_get_accounts,
    get_chat as repo_get_chat,
    get_chats as repo_get_chats,
    get_draft as repo_get_draft,
    get_folders as repo_get_folders,
    get_messages as repo_get_messages,
    mark_chats_read as repo_mark_chats_read,
    mute_chats as repo_mute_chats,
    save_draft as repo_save_draft,
    upsert_account as repo_upsert_account,
    upsert_chats as repo_upsert_chats,
    upsert_messages as repo_upsert_messages,
)
from tg_cli.telegram.gateway import Gateway
from tg_cli.telegram.worker import (
    CancelLoginCmd,
    ConnectAccountsCmd,
    DisconnectAccountCmd,
    StartLoginCmd,
    StopWorkerCmd,
    SubmitCodeCmd,
    SubmitPasswordCmd,
    TelethonWorker,
)


class TelethonGateway:
    """Production implementation of Gateway backed by Telethon and SQLite."""

    def __init__(
        self,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
        db_path: Any | None = None,
    ) -> None:
        self.db_path = db_path
        self.conn = init_db(db_path)
        self.event_callback = event_callback or (lambda evt, data: None)
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        if event_callback:
            self._listeners.append(event_callback)

        self.worker = TelethonWorker(
            event_callback=self._on_worker_event,
            db_path=db_path,
        )
        self._temp_msg_counter = -1
        self._lock = threading.Lock()

    def register_listener(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def unregister_listener(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _on_worker_event(self, event_type: str, data: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            try:
                listener(event_type, data)
            except Exception:
                pass

    def start(self) -> None:
        """Start worker thread and connect existing accounts."""
        self.worker.start()
        cfg = load_config()
        api_id = cfg.get("api_id")
        api_hash = cfg.get("api_hash")
        if api_id and api_hash:
            accounts = repo_get_accounts(self.conn)
            if accounts:
                self.worker.submit_command(
                    ConnectAccountsCmd(accounts=accounts, api_id=api_id, api_hash=api_hash)
                )

    def stop(self) -> None:
        """Gracefully stop worker thread and close database."""
        self.worker.submit_command(StopWorkerCmd())
        self.worker.join(timeout=3.0)
        self.conn.close()

    def _on_worker_event(self, event_type: str, data: dict[str, Any]) -> None:
        # Re-fetch or refresh cached connection if needed
        self.event_callback(event_type, data)

    # Gateway Protocol implementation
    def get_accounts(self) -> list[Account]:
        return repo_get_accounts(self.conn)

    def get_folders(self, account_id: int) -> list[Folder]:
        return repo_get_folders(self.conn, account_id)

    def get_chats(
        self,
        account_id: int,
        folder_id: int | None = None,
        filter_query: str = "",
    ) -> list[Chat]:
        return repo_get_chats(self.conn, account_id, folder_id, filter_query)

    def get_chat(self, account_id: int, chat_id: int) -> Chat | None:
        return repo_get_chat(self.conn, account_id, chat_id)

    def get_messages(
        self,
        account_id: int,
        chat_id: int,
        limit: int = 50,
        before_id: int | None = None,
        search_query: str = "",
    ) -> list[Message]:
        return repo_get_messages(self.conn, account_id, chat_id, limit, before_id, search_query)

    def send_message(
        self,
        account_id: int,
        chat_id: int,
        text: str,
        reply_to_id: int | None = None,
    ) -> Message:
        with self._lock:
            temp_id = self._temp_msg_counter
            self._temp_msg_counter -= 1

        msg = Message(
            account_id=account_id,
            chat_id=chat_id,
            message_id=temp_id,
            sender_id=account_id,
            sender_name="Me",
            date=datetime.now(timezone.utc),
            kind=MessageKind.TEXT,
            plain_text=text,
            reply_id=reply_to_id,
            outgoing=True,
            send_state=SendState.QUEUED,
        )
        repo_upsert_messages(self.conn, [msg])
        return msg

    def edit_message(
        self,
        account_id: int,
        chat_id: int,
        message_id: int,
        text: str,
    ) -> Message:
        msg = Message(
            account_id=account_id,
            chat_id=chat_id,
            message_id=message_id,
            plain_text=text,
            edited=True,
        )
        repo_upsert_messages(self.conn, [msg])
        return msg

    def delete_messages(
        self,
        account_id: int,
        chat_id: int,
        message_ids: Sequence[int],
    ) -> None:
        repo_delete_messages(self.conn, account_id, chat_id, message_ids)

    def archive_chats(
        self,
        account_id: int,
        chat_ids: Sequence[int],
        archived: bool = True,
    ) -> None:
        repo_archive_chats(self.conn, account_id, chat_ids, archived)

    def mute_chats(
        self,
        account_id: int,
        chat_ids: Sequence[int],
        muted: bool = True,
    ) -> None:
        repo_mute_chats(self.conn, account_id, chat_ids, muted)

    def mark_chats_read(
        self,
        account_id: int,
        chat_ids: Sequence[int],
        read: bool = True,
    ) -> None:
        repo_mark_chats_read(self.conn, account_id, chat_ids, read)

    def delete_chats(
        self,
        account_id: int,
        chat_ids: Sequence[int],
    ) -> None:
        repo_delete_chats(self.conn, account_id, chat_ids)

    def restore_chats(
        self,
        account_id: int,
        chats: Sequence[Chat],
    ) -> None:
        repo_upsert_chats(self.conn, chats)

    # Login management methods
    def start_login(self, phone: str, api_id: int, api_hash: str) -> None:
        self.worker.submit_command(
            StartLoginCmd(phone=phone, api_id=api_id, api_hash=api_hash)
        )

    def submit_login_code(self, code: str) -> None:
        self.worker.submit_command(SubmitCodeCmd(code=code))

    def submit_login_password(self, password: str) -> None:
        self.worker.submit_command(SubmitPasswordCmd(password=password))

    def cancel_login(self) -> None:
        self.worker.submit_command(CancelLoginCmd())

    def remove_account(self, user_id: int) -> None:
        self.worker.submit_command(DisconnectAccountCmd(user_id=user_id))
        session_file = get_session_path(user_id).with_suffix(".session")
        if session_file.exists():
            try:
                session_file.unlink()
            except Exception:
                pass
        from tg_cli.store.repo import delete_account
        delete_account(self.conn, user_id)

    def get_draft(self, account_id: int, chat_id: int) -> Draft | None:
        return repo_get_draft(self.conn, account_id, chat_id)

    def save_draft(self, draft: Draft) -> None:
        repo_save_draft(self.conn, draft)

    def clear_draft(self, account_id: int, chat_id: int) -> None:
        repo_clear_draft(self.conn, account_id, chat_id)

    def retry_failed_message(self, account_id: int, chat_id: int, message_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE messages SET send_state = ? WHERE account_id = ? AND chat_id = ? AND message_id = ?",
                (SendState.SENT.value, account_id, chat_id, message_id),
            )

