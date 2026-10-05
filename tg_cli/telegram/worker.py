"""Dedicated worker thread running an independent asyncio loop for Telethon.
Isolates Telethon from Textual's event loop to prevent event loop collisions.
Communicates strictly via thread-safe queues and SQLite updates.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import Any, Callable, Sequence
from telethon import TelegramClient, errors
from telethon.tl.types import User
from tg_cli.domain.models import Account, AuthState, Chat, Folder
from tg_cli.paths import get_session_path
from tg_cli.store.db import init_db
from tg_cli.store.repo import (
    delete_messages,
    get_accounts,
    get_chat as repo_get_chat,
    upsert_account,
    upsert_chats,
    upsert_folders,
    upsert_messages,
)
from tg_cli.telegram.map import (
    map_telethon_dialog_to_chat,
    map_telethon_message_to_domain,
    map_telethon_user_to_account,
)

logger = logging.getLogger("tg_cli.worker")
logger.setLevel(logging.INFO)


class WorkerCommand:
    """Base class for commands sent to the worker thread."""
    pass


class ConnectAccountsCmd(WorkerCommand):
    def __init__(self, accounts: list[Account], api_id: int, api_hash: str) -> None:
        self.accounts = accounts
        self.api_id = api_id
        self.api_hash = api_hash


class StartLoginCmd(WorkerCommand):
    def __init__(self, phone: str, api_id: int, api_hash: str) -> None:
        self.phone = phone
        self.api_id = api_id
        self.api_hash = api_hash


class SubmitCodeCmd(WorkerCommand):
    def __init__(self, code: str) -> None:
        self.code = code


class SubmitPasswordCmd(WorkerCommand):
    def __init__(self, password: str) -> None:
        self.password = password


class CancelLoginCmd(WorkerCommand):
    pass


class DisconnectAccountCmd(WorkerCommand):
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id


class StopWorkerCmd(WorkerCommand):
    pass


class TelethonWorker(threading.Thread):
    """Thread running an isolated asyncio event loop for Telethon clients."""

    def __init__(
        self,
        event_callback: Callable[[str, dict[str, Any]], None],
        db_path: Any | None = None,
    ) -> None:
        super().__init__(name="TelethonWorkerThread", daemon=True)
        self.event_callback = event_callback
        self.db_path = db_path
        self.command_queue: queue.Queue[WorkerCommand] = queue.Queue()
        self.clients: dict[int, TelegramClient] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._running = True

        # In-progress login state
        self._login_client: TelegramClient | None = None
        self._login_phone: str = ""
        self._login_phone_code_hash: str = ""
        self._login_api_id: int = 0
        self._login_api_hash: str = ""

    def run(self) -> None:
        """Entrypoint for dedicated background thread."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.conn = init_db(self.db_path)

        logger.info("Telethon worker thread started")
        try:
            self.loop.run_until_complete(self._main_loop())
        finally:
            self.loop.run_until_complete(self._shutdown())
            self.conn.close()
            self.loop.close()
            logger.info("Telethon worker thread stopped")

    def submit_command(self, cmd: WorkerCommand) -> None:
        self.command_queue.put(cmd)

    async def _main_loop(self) -> None:
        while self._running:
            # Check for commands in queue without blocking asyncio loop
            try:
                while True:
                    cmd = self.command_queue.get_nowait()
                    await self._process_command(cmd)
            except queue.Empty:
                pass

            await asyncio.sleep(0.05)

    async def _process_command(self, cmd: WorkerCommand) -> None:
        if isinstance(cmd, StopWorkerCmd):
            self._running = False
        elif isinstance(cmd, ConnectAccountsCmd):
            await self._handle_connect_accounts(cmd)
        elif isinstance(cmd, StartLoginCmd):
            await self._handle_start_login(cmd)
        elif isinstance(cmd, SubmitCodeCmd):
            await self._handle_submit_code(cmd)
        elif isinstance(cmd, SubmitPasswordCmd):
            await self._handle_submit_password(cmd)
        elif isinstance(cmd, CancelLoginCmd):
            await self._cleanup_login_client()
        elif isinstance(cmd, DisconnectAccountCmd):
            await self._disconnect_client(cmd.user_id)

    async def _handle_connect_accounts(self, cmd: ConnectAccountsCmd) -> None:
        for acc in cmd.accounts:
            if acc.user_id in self.clients:
                continue

            session_file = get_session_path(acc.user_id)
            client = TelegramClient(str(session_file), cmd.api_id, cmd.api_hash)
            try:
                await client.connect()
                if await client.is_user_authorized():
                    me = await client.get_me()
                    if me:
                        updated_acc = map_telethon_user_to_account(me, str(session_file))
                        upsert_account(self.conn, updated_acc)
                        self.clients[acc.user_id] = client
                        logger.info(f"Connected account {acc.user_id}")
                        self._attach_event_handlers(acc.user_id, client)
                        asyncio.create_task(self._sync_folders(acc.user_id, client))
                        asyncio.create_task(self._sync_dialogs(acc.user_id, client))
                        self._emit("account_connected", {"user_id": acc.user_id})
                else:
                    # Session expired
                    acc.auth_state = AuthState.EXPIRED
                    upsert_account(self.conn, acc)
                    self._emit("status", {"message": f"Session expired for {acc.label}", "is_error": True})
            except Exception as e:
                logger.warning(f"Error connecting account {acc.user_id}: {type(e).__name__}")
                self._emit("status", {"message": f"Connection error: {type(e).__name__}", "is_error": True})

    async def _handle_start_login(self, cmd: StartLoginCmd) -> None:
        await self._cleanup_login_client()

        # Temporary session file for login
        temp_session = get_session_path("temp_login")
        self._login_api_id = cmd.api_id
        self._login_api_hash = cmd.api_hash
        self._login_phone = cmd.phone

        self._login_client = TelegramClient(str(temp_session), cmd.api_id, cmd.api_hash)
        try:
            await self._login_client.connect()
            result = await self._login_client.send_code_request(cmd.phone)
            self._login_phone_code_hash = result.phone_code_hash
            logger.info("Login code requested successfully")
            self._emit("login_code_sent", {"phone": cmd.phone})
        except errors.FloodWaitError as e:
            wait_s = e.seconds
            self._emit("login_error", {"error": f"Rate limited: wait {wait_s}s"})
        except Exception as e:
            logger.warning(f"send_code_request failed: {type(e).__name__}")
            self._emit("login_error", {"error": f"Failed: {str(e)}"})

    async def _handle_submit_code(self, cmd: SubmitCodeCmd) -> None:
        if not self._login_client or not self._login_phone:
            self._emit("login_error", {"error": "No login in progress"})
            return

        try:
            user = await self._login_client.sign_in(
                phone=self._login_phone,
                code=cmd.code,
                phone_code_hash=self._login_phone_code_hash,
            )
            await self._finish_login(user)
        except errors.SessionPasswordNeededError:
            self._emit("login_2fa_needed", {"hint": "Two-step verification active"})
        except errors.PhoneCodeInvalidError:
            self._emit("login_error", {"error": "Invalid code. Please try again."})
        except errors.PhoneCodeExpiredError:
            self._emit("login_error", {"error": "Code expired. Please request a new one."})
        except errors.FloodWaitError as e:
            self._emit("login_error", {"error": f"Rate limited: wait {e.seconds}s"})
        except Exception as e:
            self._emit("login_error", {"error": f"Error: {str(e)}"})

    async def _handle_submit_password(self, cmd: SubmitPasswordCmd) -> None:
        if not self._login_client:
            self._emit("login_error", {"error": "No login in progress"})
            return

        try:
            user = await self._login_client.sign_in(password=cmd.password)
            await self._finish_login(user)
        except errors.PasswordHashInvalidError:
            self._emit("login_error", {"error": "Incorrect password. Try again."})
        except Exception as e:
            self._emit("login_error", {"error": f"2FA failed: {str(e)}"})

    async def _finish_login(self, user: Any) -> None:
        user_id = int(getattr(user, "id", 0))
        final_session = get_session_path(user_id)

        # Move/save to final session path
        if self._login_client:
            await self._login_client.disconnect()
            temp_session_file = get_session_path("temp_login").with_suffix(".session")
            final_session_file = final_session.with_suffix(".session")
            if temp_session_file.exists():
                if final_session_file.exists():
                    final_session_file.unlink()
                temp_session_file.rename(final_session_file)

        # Reopen with final session path and store
        client = TelegramClient(str(final_session), self._login_api_id, self._login_api_hash)
        await client.connect()
        me = await client.get_me()
        account = map_telethon_user_to_account(me or user, str(final_session))
        upsert_account(self.conn, account)
        self.clients[account.user_id] = client
        self._attach_event_handlers(account.user_id, client)
        asyncio.create_task(self._sync_folders(account.user_id, client))
        asyncio.create_task(self._sync_dialogs(account.user_id, client))

        self._login_client = None
        self._login_phone = ""
        self._login_phone_code_hash = ""

        logger.info(f"User {account.user_id} successfully authenticated")
        self._emit("login_success", {"account": account})

    def _attach_event_handlers(self, user_id: int, client: TelegramClient) -> None:
        """Attach live event handlers to Telethon client for real-time updates."""
        from telethon import events

        @client.on(events.NewMessage())
        async def on_new_message(event: events.NewMessage.Event):
            try:
                msg = map_telethon_message_to_domain(event.message, user_id, event.chat_id)
                upsert_messages(self.conn, [msg])

                chat = repo_get_chat(self.conn, user_id, event.chat_id)
                if chat:
                    chat.last_message_id = msg.message_id
                    chat.last_date = msg.date
                    chat.last_preview = msg.plain_text or f"[{msg.kind.value}]"
                    if not msg.outgoing:
                        chat.unread_count += 1
                    upsert_chats(self.conn, [chat])

                self._emit("chats_changed", {"user_id": user_id})
                self._emit("messages_changed", {"user_id": user_id, "chat_id": event.chat_id})
            except Exception as e:
                logger.warning(f"Error handling live message: {type(e).__name__}")

        @client.on(events.MessageEdited())
        async def on_message_edited(event: events.MessageEdited.Event):
            try:
                msg = map_telethon_message_to_domain(event.message, user_id, event.chat_id)
                upsert_messages(self.conn, [msg])
                self._emit("messages_changed", {"user_id": user_id, "chat_id": event.chat_id})
            except Exception as e:
                logger.warning(f"Error handling live edit: {type(e).__name__}")

        @client.on(events.MessageDeleted())
        async def on_message_deleted(event: events.MessageDeleted.Event):
            try:
                deleted_ids = event.deleted_ids or []
                if deleted_ids:
                    chat_id = getattr(event, "chat_id", None)
                    if chat_id:
                        delete_messages(self.conn, user_id, chat_id, deleted_ids)
                        self._emit("messages_changed", {"user_id": user_id, "chat_id": chat_id})
            except Exception as e:
                logger.warning(f"Error handling live delete: {type(e).__name__}")

    async def _sync_dialogs(self, user_id: int, client: TelegramClient) -> None:
        """Paging dialog sync without blocking UI."""
        self._emit("status", {"message": "syncing dialogs...", "is_error": False})
        batch: list[Chat] = []
        try:
            async for dialog in client.iter_dialogs(limit=None):
                chat = map_telethon_dialog_to_chat(dialog, user_id)
                folder_id = getattr(dialog, "folder_id", 0)
                if folder_id:
                    chat.folder_ids.add(folder_id)
                batch.append(chat)

                if len(batch) >= 100:
                    upsert_chats(self.conn, batch)
                    self._emit("chats_changed", {"user_id": user_id, "count": len(batch)})
                    batch.clear()
                    await asyncio.sleep(0.01)

            if batch:
                upsert_chats(self.conn, batch)
                self._emit("chats_changed", {"user_id": user_id, "count": len(batch)})

            self._emit("status", {"message": "ready", "is_error": False})
        except errors.FloodWaitError as e:
            self._emit("status", {"message": f"rate limited {e.seconds}s", "is_error": True})
        except Exception as e:
            logger.warning(f"Error syncing dialogs for {user_id}: {type(e).__name__}")
            self._emit("status", {"message": f"sync error: {type(e).__name__}", "is_error": True})

    async def _sync_folders(self, user_id: int, client: TelegramClient) -> None:
        """Fetch Telegram folder tabs."""
        try:
            from telethon.tl.functions.messages import GetDialogFiltersRequest
            from telethon.tl.types import DialogFilter, DialogFilterChatlist

            filters = await client(GetDialogFiltersRequest())
            domain_folders: list[Folder] = []
            for pos, f in enumerate(filters):
                if isinstance(f, (DialogFilter, DialogFilterChatlist)):
                    title = getattr(f, "title", f"Folder {f.id}")
                    if hasattr(title, "text"):
                        title = title.text
                    domain_folders.append(
                        Folder(
                            account_id=user_id,
                            folder_id=f.id,
                            title=str(title),
                            position=pos + 1,
                        )
                    )
            if domain_folders:
                upsert_folders(self.conn, domain_folders)
                self._emit("folders_changed", {"user_id": user_id})
        except Exception as e:
            logger.debug(f"Could not fetch dialog filters: {type(e).__name__}")

    async def _cleanup_login_client(self) -> None:
        if self._login_client:
            try:
                await self._login_client.disconnect()
            except Exception:
                pass
            self._login_client = None
        self._login_phone = ""
        self._login_phone_code_hash = ""
        temp_file = get_session_path("temp_login").with_suffix(".session")
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass

    async def _disconnect_client(self, user_id: int) -> None:
        client = self.clients.pop(user_id, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def _shutdown(self) -> None:
        await self._cleanup_login_client()
        for cid, client in list(self.clients.items()):
            try:
                await client.disconnect()
            except Exception:
                pass
        self.clients.clear()

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        try:
            self.event_callback(event_type, data)
        except Exception as e:
            logger.warning(f"Error dispatching event {event_type}: {e}")
