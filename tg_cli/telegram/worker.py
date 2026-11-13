"""Dedicated worker thread running an independent asyncio loop for Telethon.
Isolates Telethon from Textual's event loop to prevent event loop collisions.
Communicates strictly via thread-safe queues and SQLite updates.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Sequence
from telethon import TelegramClient, errors
from telethon.sessions import SQLiteSession
from telethon.tl.types import User
from tg_cli.config import get_proxy_config
from tg_cli.domain.files import send_groups, upload_label, upload_progress_line, upload_weights
from tg_cli.domain.models import (
    Account,
    AuthState,
    Chat,
    Folder,
    Message,
    SendState,
    SessionInfo,
    UserProfile,
)
from tg_cli.paths import get_session_path
from tg_cli.store.db import init_db
from tg_cli.store.repo import (
    archive_chats as repo_archive_chats,
    claim_due_chat_deletes,
    delete_chats as repo_delete_chats,
    delete_messages,
    dialog_state_needs_push,
    get_accounts,
    get_chat as repo_get_chat,
    mark_dialog_state_pushed,
    mute_chats as repo_mute_chats,
    pending_delete_ids,
    reschedule_chat_delete,
    saved_archive_and_mute_ids,
    upsert_account,
    upsert_chats,
    upsert_folders,
    upsert_messages,
)
from tg_cli.telegram.map import (
    map_telethon_dialog_to_chat,
    map_telethon_message_to_domain,
    map_telethon_user_to_account,
    member_role,
    notify_is_muted,
)

logger = logging.getLogger("tg_cli.worker")
logger.setLevel(logging.INFO)


class DurableSession(SQLiteSession):
    """Session file that waits out a short lock and then releases it.

    Telethon leaves a write transaction open after saving entities. A second
    window, or a file upload on this one, then fails with "database is locked"
    and the send never leaves the machine.
    """

    def _cursor(self):
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.filename,
                timeout=30.0,
                check_same_thread=False,
            )
            self._conn.execute("PRAGMA busy_timeout=30000")
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
        return self._conn.cursor()

    def _execute(self, stmt, *values):
        row = super()._execute(stmt, *values)
        self._release_lock()
        return row

    def process_entities(self, tlo):
        try:
            super().process_entities(tlo)
        except sqlite3.Error:
            self._rollback()
            raise
        self._release_lock()

    def _release_lock(self) -> None:
        try:
            self.save()
        except sqlite3.Error:
            self._rollback()

    def _rollback(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.rollback()
        except sqlite3.Error:
            pass


def create_telegram_client(
    session: Any,
    api_id: int,
    api_hash: str,
) -> TelegramClient:
    """Instantiate a TelegramClient with MTProto/SOCKS5/HTTP proxy support if enabled."""
    session_obj = DurableSession(str(session))
    p_cfg = get_proxy_config()
    if p_cfg.get("enabled") and p_cfg.get("addr") and p_cfg.get("port"):
        p_type = p_cfg.get("type", "socks5").lower()
        addr = str(p_cfg.get("addr"))
        port = int(p_cfg.get("port"))
        user = p_cfg.get("username") or None
        password = p_cfg.get("password") or None
        secret = p_cfg.get("secret") or None

        if p_type in ("socks5", "socks4", "http"):
            import socks
            ptype = socks.SOCKS5 if p_type == "socks5" else (socks.SOCKS4 if p_type == "socks4" else socks.HTTP)
            return TelegramClient(
                session_obj,
                api_id,
                api_hash,
                proxy=(ptype, addr, port, True, user, password),
            )
        elif p_type == "mtproto":
            from telethon.network import connection
            return TelegramClient(
                session_obj,
                api_id,
                api_hash,
                connection=connection.ConnectionTcpMTProxyRandomizedIntermediate,
                proxy=(addr, port, secret),
            )

    return TelegramClient(session_obj, api_id, api_hash)


_NEXT_CODE_LABELS = {
    "CodeTypeSms": "an SMS",
    "CodeTypeCall": "a voice call",
    "CodeTypeFlashCall": "a flash call",
    "CodeTypeMissedCall": "a missed call",
    "CodeTypeFragmentSms": "a Fragment SMS",
}


def describe_sent_code(result: Any, phone: str) -> dict[str, Any]:
    """Say where Telegram actually put the login code.

    ``auth.sendCode`` returning is not the same as an SMS existing.
    The payload never includes ``phone_code_hash``.
    """
    type_name = type(result).__name__
    if type_name == "SentCodePaymentRequired":
        return {
            "phone": phone,
            "delivery": "payment",
            "delivered": False,
            "length": None,
            "resend_in": 0,
            "can_resend": False,
            "next": "",
            "hint": (
                "Telegram requires a payment before it will send a login code "
                "for this number."
            ),
        }

    sent_type = getattr(result, "type", None)
    delivery_name = type(sent_type).__name__ if sent_type is not None else ""
    timeout = getattr(result, "timeout", None)
    resend_in = int(timeout) if isinstance(timeout, int) and timeout > 0 else 0
    next_type = getattr(result, "next_type", None)
    next_name = type(next_type).__name__ if next_type is not None else ""
    length = getattr(sent_type, "length", None)
    if not isinstance(length, int) or length <= 0:
        length = None
    note = _resend_note(next_type, resend_in)
    digits = f"{length}-digit " if length else ""
    # A missing next type and a missing wait means auth.resendCode has no method to send.
    offered_next = bool(next_name) or resend_in > 0

    if delivery_name == "SentCodeTypeApp" and not offered_next:
        hint = (
            "Telegram accepted this number and did not send an SMS or a call.\n"
            "It also is not delivering an in-app code to the phone already signed into this number.\n"
            "Add it from that phone: Ctrl+Q, then Settings, Devices, Link Desktop Device.\n"
            "Scan with the phone logged into this number. The other phone adds the other account."
        )
        delivery, delivered = "app", False
    elif delivery_name == "SentCodeTypeApp":
        length_sentence = f" It is {length} digits." if length else ""
        when = (
            f"If that session shows nothing, this app requests SMS or a call in {resend_in}s."
            if resend_in
            else "Telegram did not offer SMS or a call for this request."
        )
        hint = (
            "Telegram sent an in-app code for the number you typed."
            f"{length_sentence}\n"
            "Open Telegram where that number is already signed in and read the message from Telegram.\n"
            f"{when}\n"
            "Ctrl+Q adds whichever account scans the QR, including a second account."
        )
        delivery, delivered = "app", True
    elif delivery_name == "SentCodeTypeSms":
        hint = f"Telegram sent a {digits}code by SMS to {phone}.\n{note}"
        delivery, delivered = "sms", True
    elif delivery_name == "SentCodeTypeCall":
        hint = (
            f"Telegram is calling {phone}. Listen for the {digits}code.\n{note}"
        )
        delivery, delivered = "call", True
    elif delivery_name == "SentCodeTypeMissedCall":
        prefix = getattr(sent_type, "prefix", "") or ""
        tail = f"the last {length} digits" if length else "the last digits"
        where = f"from a number starting with {prefix}" if prefix else "you"
        hint = (
            f"Do not answer the call. Telegram will ring {where}. "
            f"Enter {tail} of that number.\n{note}"
        )
        delivery, delivered = "missed_call", True
    elif delivery_name == "SentCodeTypeFlashCall":
        pattern = getattr(sent_type, "pattern", "") or ""
        matching = f" matching {pattern}" if pattern else ""
        hint = (
            f"Telegram will flash-call a number{matching}. "
            f"The code is the caller id.\n{note}"
        )
        delivery, delivered = "flash_call", True
    elif delivery_name == "SentCodeTypeEmailCode":
        pattern = getattr(sent_type, "email_pattern", "") or "your login email"
        hint = f"Telegram sent a {digits}code to {pattern}.\n{note}"
        delivery, delivered = "email", True
    elif delivery_name == "SentCodeTypeFragmentSms":
        url = getattr(sent_type, "url", "") or "https://fragment.com"
        hint = (
            f"This number is on Fragment. The {digits}code is at:\n{url}\n{note}"
        )
        delivery, delivered = "fragment", True
    elif delivery_name == "SentCodeTypeSmsWord":
        beginning = getattr(sent_type, "beginning", None)
        starts = f' It starts with "{beginning}".' if beginning else ""
        hint = (
            "Telegram sent a word by SMS, not a numeric code. "
            f"Type that word.{starts}\n{note}"
        )
        delivery, delivered = "sms_word", True
    elif delivery_name == "SentCodeTypeSmsPhrase":
        beginning = getattr(sent_type, "beginning", None)
        starts = f' It starts with "{beginning}".' if beginning else ""
        hint = (
            "Telegram sent a phrase by SMS, not a numeric code. "
            f"Type that phrase.{starts}\n{note}"
        )
        delivery, delivered = "sms_phrase", True
    elif delivery_name == "SentCodeTypeFirebaseSms":
        when = (
            f"Requesting another method in {resend_in}s."
            if resend_in
            else "Requesting another method now."
        )
        hint = (
            f"Telegram accepted {phone} but did not send a code.\n"
            "It asked for a phone-integrity check this app cannot complete, "
            "so no SMS was sent.\n"
            f"{when} Ctrl+Q switches to QR login."
        )
        delivery, delivered = "firebase", False
    elif delivery_name == "SentCodeTypeSetUpEmailRequired":
        hint = (
            "Telegram will not send a code until this account has a login email.\n"
            "Set one in the official app, then try again. Ctrl+Q uses QR login."
        )
        delivery, delivered = "email_setup", False
    else:
        shown = delivery_name or type_name or "unknown"
        hint = (
            f"Telegram accepted the request ({shown}) but this app does not "
            f"know where the code went.\n{note}"
        )
        delivery, delivered = "unknown", False

    # SMS and calls can still be asked for again. An in-app code with no next method cannot.
    can_resend = offered_next or delivery not in ("app", "email_setup")

    return {
        "phone": phone,
        "delivery": delivery,
        "delivered": delivered,
        "length": length,
        "resend_in": resend_in,
        "can_resend": can_resend,
        "next": next_name,
        "hint": hint,
    }


def _resend_note(next_type: Any, resend_in: int) -> str:
    label = _NEXT_CODE_LABELS.get(type(next_type).__name__) if next_type is not None else None
    if label and resend_in > 0:
        return f"Ctrl+R requests {label} after {resend_in}s. Ctrl+Q is QR login."
    if label:
        return f"Ctrl+R requests {label} instead. Ctrl+Q is QR login."
    if resend_in > 0:
        return f"Ctrl+R asks for another method after {resend_in}s. Ctrl+Q is QR login."
    return "Ctrl+R asks for another method. Ctrl+Q is QR login."


def login_code_needs_followup(delivery: str, resend_in: int = 0) -> bool:
    """True when this client should ask Telegram for the next delivery method.

    Firebase means nothing was sent. An in-app code is followed up only after
    Telegram's own wait. A missing wait means no fallback was offered, and
    calling resend immediately drops the only code.
    """
    if delivery == "firebase":
        return True
    return delivery == "app" and resend_in > 0


_LOGIN_CODE_RE = re.compile(r"(?<!\d)(\d{5,6})(?!\d)")


def extract_login_code(text: str) -> str:
    """Return the 5- or 6-digit login code in a Telegram service message."""
    match = _LOGIN_CODE_RE.search(text or "")
    return match.group(1) if match else ""


def read_session_dc(user_id: int) -> int | None:
    """Return the data center stored for a signed-in account, if the session exists.

    Opens the file read-only. A write connection here locks the session the
    live client is using, and the next photo upload fails.
    """
    path = get_session_path(user_id)
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("select dc_id from sessions").fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not row or row[0] is None:
        return None
    return int(row[0])


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


class ResendLoginCodeCmd(WorkerCommand):
    pass


class StartQrLoginCmd(WorkerCommand):
    def __init__(self, api_id: int, api_hash: str) -> None:
        self.api_id = api_id
        self.api_hash = api_hash


class CancelQrLoginCmd(WorkerCommand):
    pass


class GetSessionsCmd(WorkerCommand):
    def __init__(self, user_id: int, result_queue: queue.Queue) -> None:
        self.user_id = user_id
        self.result_queue = result_queue


class RevokeSessionCmd(WorkerCommand):
    def __init__(self, user_id: int, session_hash: int, result_queue: queue.Queue) -> None:
        self.user_id = user_id
        self.session_hash = session_hash
        self.result_queue = result_queue


class GetProfileCmd(WorkerCommand):
    def __init__(self, user_id: int, result_queue: queue.Queue) -> None:
        self.user_id = user_id
        self.result_queue = result_queue


class UpdateProfileCmd(WorkerCommand):
    def __init__(
        self,
        user_id: int,
        bio: str | None,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        result_queue: queue.Queue,
    ) -> None:
        self.user_id = user_id
        self.bio = bio
        self.username = username
        self.first_name = first_name
        self.last_name = last_name
        self.result_queue = result_queue


class DisconnectAccountCmd(WorkerCommand):
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id


class StopWorkerCmd(WorkerCommand):
    pass


class FetchMessagesCmd(WorkerCommand):
    def __init__(
        self,
        account_id: int,
        chat_id: int,
        limit: int = 50,
        before_id: int | None = None,
    ) -> None:
        self.account_id = account_id
        self.chat_id = chat_id
        self.limit = limit
        self.before_id = before_id


class FetchRolesCmd(WorkerCommand):
    def __init__(self, account_id: int, chat_id: int) -> None:
        self.account_id = account_id
        self.chat_id = chat_id


class SendMessageCmd(WorkerCommand):
    def __init__(
        self,
        account_id: int,
        chat_id: int,
        temp_msg_id: int,
        text: str,
        reply_to_id: int | None = None,
    ) -> None:
        self.account_id = account_id
        self.chat_id = chat_id
        self.temp_msg_id = temp_msg_id
        self.text = text
        self.reply_to_id = reply_to_id


class SendFilesCmd(WorkerCommand):
    def __init__(
        self,
        account_id: int,
        chat_id: int,
        temp_ids: Sequence[int],
        paths: Sequence[str],
        as_file: bool,
        caption: str = "",
        reply_to_id: int | None = None,
    ) -> None:
        self.account_id = account_id
        self.chat_id = chat_id
        self.temp_ids = list(temp_ids)
        self.paths = list(paths)
        self.as_file = as_file
        self.caption = caption
        self.reply_to_id = reply_to_id


class EditMessageCmd(WorkerCommand):
    def __init__(
        self,
        account_id: int,
        chat_id: int,
        message_id: int,
        text: str,
    ) -> None:
        self.account_id = account_id
        self.chat_id = chat_id
        self.message_id = message_id
        self.text = text


class DeleteMessagesCmd(WorkerCommand):
    def __init__(
        self,
        account_id: int,
        chat_id: int,
        message_ids: Sequence[int],
    ) -> None:
        self.account_id = account_id
        self.chat_id = chat_id
        self.message_ids = message_ids


class MarkChatReadCmd(WorkerCommand):
    def __init__(
        self,
        account_id: int,
        chat_ids: Sequence[int],
    ) -> None:
        self.account_id = account_id
        self.chat_ids = chat_ids


class ArchiveChatsCmd(WorkerCommand):
    def __init__(
        self,
        account_id: int,
        chat_ids: Sequence[int],
        archived: bool,
    ) -> None:
        self.account_id = account_id
        self.chat_ids = list(chat_ids)
        self.archived = archived


class MuteChatsCmd(WorkerCommand):
    def __init__(
        self,
        account_id: int,
        chat_ids: Sequence[int],
        muted: bool,
    ) -> None:
        self.account_id = account_id
        self.chat_ids = list(chat_ids)
        self.muted = muted


# Telegram stores "mute forever" as this unix time.
MUTE_UNTIL_FOREVER = 2**31 - 1
_NOT_CONNECTED = "Not connected, so nothing was sent. Close any other tg-cli window and retry."
_FOLDER_BATCH = 100
_DELETE_BATCH = 10
_DELETE_ATTEMPTS = 3


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
        self._api_id: int | None = None
        self._api_hash: str | None = None

        # In-progress login state
        self._login_client: TelegramClient | None = None
        self._login_phone: str = ""
        self._login_phone_code_hash: str = ""
        self._login_api_id: int = 0
        self._login_api_hash: str = ""
        self._qr_login: Any = None
        self._qr_task: asyncio.Task | None = None
        self._login_resend_task: asyncio.Task | None = None
        self._login_resend_not_before: float = 0.0
        self._login_can_resend: bool = True
        self._login_auto_resends: int = 0
        self._login_generation: int = 0
        self._login_watch_active: bool = False
        self._login_code_watchers: list[tuple[Any, Any]] = []
        self._next_delete_flush = 0.0
        self._deletes_in_flight: set[tuple[int, int]] = set()

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

            if time.monotonic() >= self._next_delete_flush:
                self._next_delete_flush = time.monotonic() + 1.0
                await self._flush_pending_deletes()

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
        elif isinstance(cmd, ResendLoginCodeCmd):
            await self._handle_resend_login_code()
        elif isinstance(cmd, StartQrLoginCmd):
            await self._handle_start_qr_login(cmd)
        elif isinstance(cmd, CancelQrLoginCmd):
            await self._cleanup_login_client()
        elif isinstance(cmd, GetSessionsCmd):
            await self._handle_get_sessions(cmd)
        elif isinstance(cmd, RevokeSessionCmd):
            await self._handle_revoke_session(cmd)
        elif isinstance(cmd, GetProfileCmd):
            await self._handle_get_profile(cmd)
        elif isinstance(cmd, UpdateProfileCmd):
            await self._handle_update_profile(cmd)
        elif isinstance(cmd, DisconnectAccountCmd):
            await self._disconnect_client(cmd.user_id)
        elif isinstance(cmd, FetchMessagesCmd):
            await self._handle_fetch_messages(cmd)
        elif isinstance(cmd, FetchRolesCmd):
            await self._handle_fetch_roles(cmd)
        elif isinstance(cmd, SendMessageCmd):
            await self._handle_send_message(cmd)
        elif isinstance(cmd, SendFilesCmd):
            await self._handle_send_files(cmd)
        elif isinstance(cmd, EditMessageCmd):
            await self._handle_edit_message(cmd)
        elif isinstance(cmd, DeleteMessagesCmd):
            await self._handle_delete_messages(cmd)
        elif isinstance(cmd, MarkChatReadCmd):
            await self._handle_mark_chat_read(cmd)
        elif isinstance(cmd, ArchiveChatsCmd):
            await self._handle_archive_chats(cmd)
        elif isinstance(cmd, MuteChatsCmd):
            await self._handle_mute_chats(cmd)

    async def _handle_connect_accounts(self, cmd: ConnectAccountsCmd) -> None:
        self._api_id = cmd.api_id
        self._api_hash = cmd.api_hash
        for acc in cmd.accounts:
            await self._connect_account(acc, cmd.api_id, cmd.api_hash)

    async def _reconnect_account(self, account_id: int) -> None:
        """Open the account again after a send found no live client."""
        if account_id in self.clients or self._api_id is None or not self._api_hash:
            return
        acc = next((item for item in get_accounts(self.conn) if item.user_id == account_id), None)
        if acc is None:
            return
        await self._connect_account(acc, self._api_id, self._api_hash, announce=False)

    async def _connect_account(
        self,
        acc: Account,
        api_id: int,
        api_hash: str,
        *,
        announce: bool = True,
    ) -> bool:
        """Connect one account. A failed attempt closes the session file."""
        if acc.user_id in self.clients:
            return True
        session_file = get_session_path(acc.user_id)
        client = create_telegram_client(session_file, api_id, api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                acc.auth_state = AuthState.EXPIRED
                upsert_account(self.conn, acc)
                await self._discard_client(client)
                if announce:
                    self._emit("status", {"message": f"Session expired for {acc.label}", "is_error": True})
                return False
            me = await client.get_me()
            if me:
                updated_acc = map_telethon_user_to_account(me, str(session_file))
                upsert_account(self.conn, updated_acc)
            self.clients[acc.user_id] = client
            logger.info(f"Connected account {acc.user_id}")
            self._attach_event_handlers(acc.user_id, client)
            await self._push_saved_dialog_state(acc.user_id)
            asyncio.create_task(self._sync_folders(acc.user_id, client))
            asyncio.create_task(self._sync_dialogs(acc.user_id, client))
            self._emit("account_connected", {"user_id": acc.user_id})
            return True
        except Exception as e:
            logger.warning(
                "Error connecting account %s: %s: %s",
                acc.user_id,
                type(e).__name__,
                e,
            )
            await self._discard_client(client)
            if announce:
                self._emit(
                    "status",
                    {"message": f"Connection error: {type(e).__name__}", "is_error": True},
                )
            return False

    async def _discard_client(self, client: TelegramClient) -> None:
        """Drop a client that did not join self.clients, and release its session."""
        try:
            await client.disconnect()
        except Exception:
            pass
        session = getattr(client, "session", None)
        close = getattr(session, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception:
            pass

    async def _handle_start_login(self, cmd: StartLoginCmd) -> None:
        await self._cleanup_login_client()

        # Temporary session file for login
        temp_session = get_session_path("temp_login")
        self._login_api_id = cmd.api_id
        self._login_api_hash = cmd.api_hash
        self._login_phone = cmd.phone

        self._login_auto_resends = 0
        self._login_can_resend = True
        self._login_client = create_telegram_client(temp_session, cmd.api_id, cmd.api_hash)
        try:
            await self._login_client.connect()
            result = await self._request_login_code(cmd.phone, cmd.api_id, cmd.api_hash)
            self._publish_sent_code(result, allow_auto_resend=True)
        except errors.FloodWaitError as e:
            wait_s = e.seconds
            self._emit("login_error", {"error": f"Rate limited: wait {wait_s}s"})
        except Exception as e:
            logger.warning(f"send_code_request failed: {type(e).__name__}")
            self._emit("login_error", {"error": f"Failed: {str(e)}"})

    async def _request_login_code(self, phone: str, api_id: int, api_hash: str) -> Any:
        """Ask Telegram for a login code, allowing a call if the in-app message never appears."""
        from telethon import utils
        from telethon.tl.functions.auth import SendCodeRequest
        from telethon.tl.types import CodeSettings

        phone = utils.parse_phone(phone) or phone
        self._login_phone = phone
        await self._prepare_matched_login(phone)
        settings = CodeSettings(allow_flashcall=True, allow_missed_call=True)
        last_error: Exception | None = None
        for _ in range(3):
            try:
                return await self._login_client(SendCodeRequest(phone, api_id, api_hash, settings))
            except errors.AuthRestartError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("Telegram did not answer the code request")

    async def _prepare_matched_login(self, phone: str) -> None:
        """Send the code on the same data center as the already signed-in account."""
        typed = re.sub(r"\D", "", phone or "")
        tail = typed[-2:] if len(typed) >= 2 else ""
        matched = None
        for account in get_accounts(self.conn):
            if typed and re.sub(r"\D", "", account.phone or "") == typed:
                matched = account
                break
        if matched is None:
            logger.info("Login phone digits=%s tail=%s matches_saved=no", len(typed), tail)
            return
        dc_id = read_session_dc(matched.user_id)
        logger.info(
            "Login phone digits=%s tail=%s matches_saved=yes dc=%s",
            len(typed),
            tail,
            dc_id,
        )
        client = self._login_client
        if client is not None and dc_id and getattr(client.session, "dc_id", None) != dc_id:
            try:
                await client._switch_dc(dc_id)
            except Exception as exc:
                logger.warning("Could not move login to dc %s: %s", dc_id, type(exc).__name__)
        self._arm_login_code_watch(matched.user_id)

    def _arm_login_code_watch(self, user_id: int) -> None:
        """Watch the open session. Telegram delivers the code there as a service message."""
        self._clear_login_code_watchers()
        client = self.clients.get(user_id)
        if client is None:
            return
        from telethon import events

        async def on_service_message(event: Any) -> None:
            self._offer_login_code(getattr(event, "raw_text", "") or "")

        async def on_raw(update: Any) -> None:
            if type(update).__name__ != "UpdateServiceNotification":
                return
            self._offer_login_code(getattr(update, "message", "") or "")

        client.add_event_handler(on_service_message, events.NewMessage(from_users=777000))
        client.add_event_handler(on_raw, events.Raw)
        self._login_code_watchers = [(client, on_service_message), (client, on_raw)]
        self._login_watch_active = True

    def _offer_login_code(self, text: str) -> None:
        if not self._login_watch_active:
            return
        code = extract_login_code(text)
        if not code:
            return
        logger.info("Login code became visible on an already signed-in session")
        self._emit("login_code_visible", {"code": code})

    def _clear_login_code_watchers(self) -> None:
        self._login_watch_active = False
        watchers = self._login_code_watchers
        self._login_code_watchers = []
        for client, callback in watchers:
            try:
                client.remove_event_handler(callback)
            except Exception:
                pass

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

    async def _handle_resend_login_code(self) -> None:
        if not self._login_client or not self._login_phone or not self._login_phone_code_hash:
            self._emit("login_error", {"error": "No login in progress"})
            return
        if not self._login_can_resend:
            self._emit(
                "login_error",
                {
                    "error": (
                        "Telegram has no other way to send this code. "
                        "Use QR login (Ctrl+Q) and scan from the phone logged into this number."
                    )
                },
            )
            return
        remaining = self._login_resend_not_before - time.monotonic()
        if remaining > 0:
            self._emit(
                "login_error",
                {"error": f"Telegram will not send another code for {int(remaining) + 1}s"},
            )
            return
        self._cancel_login_resend_task()
        await self._resend_login_code(self._login_generation)

    async def _auto_resend_login_code(self, delay: float, generation: int) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            if generation != self._login_generation:
                return
            logger.info("Login code follow-up starting")
            await self._resend_login_code(generation)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("Login code follow-up failed: %s", type(exc).__name__)

    async def _resend_login_code(self, generation: int) -> None:
        client = self._login_client
        phone = self._login_phone
        phone_hash = self._login_phone_code_hash
        if generation != self._login_generation or not client or not phone or not phone_hash:
            return
        from telethon.tl.functions.auth import ResendCodeRequest

        try:
            result = await client(ResendCodeRequest(phone, phone_hash))
        except asyncio.CancelledError:
            raise
        except errors.FloodWaitError as e:
            if generation == self._login_generation:
                self._emit("login_error", {"error": f"Rate limited: wait {e.seconds}s"})
            return
        except errors.PhoneCodeExpiredError:
            if generation == self._login_generation:
                self._emit(
                    "login_error",
                    {"error": "Code request expired. Enter the phone number again."},
                )
            return
        except errors.SendCodeUnavailableError:
            if generation == self._login_generation:
                self._emit(
                    "login_error",
                    {"error": "Telegram has no other way to send this code. Use QR login (Ctrl+Q)."},
                )
            return
        except Exception as e:
            logger.warning(f"resend code failed: {type(e).__name__}")
            if generation == self._login_generation:
                self._emit("login_error", {"error": f"Could not resend the code: {type(e).__name__}"})
            return
        if generation != self._login_generation:
            return
        self._publish_sent_code(result, allow_auto_resend=True)

    def _publish_sent_code(self, result: Any, *, allow_auto_resend: bool) -> None:
        type_name = type(result).__name__ if result is not None else ""
        if type_name not in ("SentCode", "SentCodePaymentRequired"):
            self._emit("login_error", {"error": "Telegram did not return a login code."})
            return
        phone_hash = getattr(result, "phone_code_hash", "") or ""
        if phone_hash:
            self._login_phone_code_hash = phone_hash
        info = describe_sent_code(result, self._login_phone)
        self._login_can_resend = bool(info.get("can_resend", True))
        self._login_resend_not_before = time.monotonic() + info["resend_in"]
        self._cancel_login_resend_task()
        if (
            allow_auto_resend
            and login_code_needs_followup(info["delivery"], info["resend_in"])
            and self._login_auto_resends < 2
            and self._login_client is not None
        ):
            self._login_auto_resends += 1
            generation = self._login_generation
            self._login_resend_task = asyncio.create_task(
                self._auto_resend_login_code(float(info["resend_in"]), generation)
            )
        elif info["delivery"] == "firebase":
            info["delivered"] = False
            info["resend_in"] = 0
            info["hint"] = (
                "Telegram still has not sent a code. It keeps requiring a phone check this app cannot do.\n"
                "QR login (Ctrl+Q) signs in the account that scans it."
            )
        elif info["delivery"] == "app" and self._login_auto_resends > 0:
            info["delivered"] = False
            info["can_resend"] = False
            self._login_can_resend = False
            info["resend_in"] = 0
            info["hint"] = (
                "Telegram still only offers an in-app code, and that message is not arriving.\n"
                "Ctrl+Q, then scan from the phone logged into this number."
            )
        logger.info(
            "Login code delivery: %s resend_in=%s next=%s",
            info["delivery"],
            info["resend_in"],
            info.get("next") or "none",
        )
        self._emit("login_code_sent", info)

    def _cancel_login_resend_task(self) -> None:
        task = self._login_resend_task
        self._login_resend_task = None
        if task is not None and not task.done():
            task.cancel()

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

    async def _handle_start_qr_login(self, cmd: StartQrLoginCmd) -> None:
        await self._cleanup_login_client()
        temp_session = get_session_path("temp_login")
        self._login_api_id = cmd.api_id
        self._login_api_hash = cmd.api_hash

        self._login_client = create_telegram_client(temp_session, cmd.api_id, cmd.api_hash)
        try:
            await self._login_client.connect()
            self._qr_login = await self._login_client.qr_login()
            exp_sec = 30
            if getattr(self._qr_login, "expires", None):
                now_utc = datetime.now(timezone.utc)
                exp_sec = max(5, int((self._qr_login.expires - now_utc).total_seconds()))
            self._emit("qr_login_token", {
                "url": self._qr_login.url,
                "expires_in": exp_sec,
            })
            if self._qr_task and not self._qr_task.done():
                self._qr_task.cancel()
            self._qr_task = asyncio.create_task(self._wait_qr_login_loop())
        except Exception as e:
            logger.warning(f"start_qr_login failed: {e}")
            self._emit("login_error", {"error": f"QR login failed: {str(e)}"})

    async def _wait_qr_login_loop(self) -> None:
        while self._qr_login and self._login_client:
            try:
                user = await self._qr_login.wait()
                await self._finish_login(user)
                break
            except asyncio.TimeoutError:
                if self._qr_login and self._login_client:
                    try:
                        await self._qr_login.recreate()
                        exp_sec = 30
                        if getattr(self._qr_login, "expires", None):
                            now_utc = datetime.now(timezone.utc)
                            exp_sec = max(5, int((self._qr_login.expires - now_utc).total_seconds()))
                        self._emit("qr_login_token", {
                            "url": self._qr_login.url,
                            "expires_in": exp_sec,
                        })
                    except Exception as err:
                        self._emit("login_error", {"error": f"QR refresh error: {str(err)}"})
                        break
            except errors.SessionPasswordNeededError:
                self._emit("login_2fa_needed", {"hint": "Two-step verification active"})
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._emit("login_error", {"error": f"QR error: {str(e)}"})
                break

    async def _handle_get_sessions(self, cmd: GetSessionsCmd) -> None:
        client = self.clients.get(cmd.user_id)
        if not client:
            cmd.result_queue.put([])
            return
        try:
            from telethon.tl.functions.account import GetAuthorizationsRequest
            res = await client(GetAuthorizationsRequest())
            sessions: list[SessionInfo] = []
            for a in getattr(res, "authorizations", []):
                sessions.append(
                    SessionInfo(
                        hash=getattr(a, "hash", 0),
                        device_model=getattr(a, "device_model", "") or "Unknown Device",
                        platform=getattr(a, "platform", "") or "",
                        system_version=getattr(a, "system_version", "") or "",
                        ip=getattr(a, "ip", "") or "",
                        country=getattr(a, "country", "") or "",
                        date_active=getattr(a, "date_active", None),
                        date_created=getattr(a, "date_created", None),
                        is_current=bool(getattr(a, "current", False)),
                        app_name=getattr(a, "app_name", "") or "",
                        app_version=getattr(a, "app_version", "") or "",
                    )
                )
            cmd.result_queue.put(sessions)
        except Exception as e:
            logger.warning(f"Error fetching authorizations: {e}")
            cmd.result_queue.put([])

    async def _handle_revoke_session(self, cmd: RevokeSessionCmd) -> None:
        client = self.clients.get(cmd.user_id)
        if not client:
            cmd.result_queue.put(False)
            return
        try:
            from telethon.tl.functions.account import ResetAuthorizationRequest
            await client(ResetAuthorizationRequest(hash=cmd.session_hash))
            cmd.result_queue.put(True)
        except Exception as e:
            logger.warning(f"Error revoking authorization: {e}")
            cmd.result_queue.put(False)

    async def _handle_get_profile(self, cmd: GetProfileCmd) -> None:
        client = self.clients.get(cmd.user_id)
        if not client:
            cmd.result_queue.put(UserProfile(user_id=cmd.user_id, first_name="User"))
            return
        try:
            me = await client.get_me()
            from telethon.tl.functions.users import GetFullUserRequest
            from telethon.tl.types import InputUserSelf
            full = await client(GetFullUserRequest(InputUserSelf()))
            bio = getattr(full.full_user, "about", "") or ""
            prof = UserProfile(
                user_id=cmd.user_id,
                first_name=getattr(me, "first_name", "") or "",
                last_name=getattr(me, "last_name", "") or "",
                username=getattr(me, "username", "") or "",
                phone=getattr(me, "phone", "") or "",
                bio=bio,
                status_emoji="",
                is_online=True,
            )
            cmd.result_queue.put(prof)
        except Exception as e:
            logger.warning(f"Error getting profile: {e}")
            cmd.result_queue.put(UserProfile(user_id=cmd.user_id, first_name="User"))

    async def _handle_update_profile(self, cmd: UpdateProfileCmd) -> None:
        client = self.clients.get(cmd.user_id)
        if not client:
            cmd.result_queue.put(UserProfile(user_id=cmd.user_id, first_name="User"))
            return
        try:
            from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest
            if cmd.bio is not None or cmd.first_name is not None or cmd.last_name is not None:
                kwargs: dict[str, Any] = {}
                if cmd.bio is not None:
                    kwargs["about"] = cmd.bio
                if cmd.first_name is not None:
                    kwargs["first_name"] = cmd.first_name
                if cmd.last_name is not None:
                    kwargs["last_name"] = cmd.last_name
                await client(UpdateProfileRequest(**kwargs))

            if cmd.username is not None:
                await client(UpdateUsernameRequest(username=cmd.username))

            # Fetch updated profile
            me = await client.get_me()
            prof = UserProfile(
                user_id=cmd.user_id,
                first_name=getattr(me, "first_name", "") or "",
                last_name=getattr(me, "last_name", "") or "",
                username=getattr(me, "username", "") or "",
                phone=getattr(me, "phone", "") or "",
                bio=cmd.bio or "",
                status_emoji="",
                is_online=True,
            )
            cmd.result_queue.put(prof)
        except Exception as e:
            logger.warning(f"Error updating profile: {e}")
            cmd.result_queue.put(UserProfile(user_id=cmd.user_id, first_name="User"))

    async def _handle_fetch_messages(self, cmd: FetchMessagesCmd) -> None:
        done: dict[str, Any] = {
            "user_id": cmd.account_id,
            "chat_id": cmd.chat_id,
            "before_id": cmd.before_id,
            "count": 0,
            "exhausted": False,
        }
        client = self.clients.get(cmd.account_id)
        if not client:
            done["error"] = "Client not connected"
            self._emit("fetch_messages_done", done)
            return

        try:
            entity = cmd.chat_id
            try:
                entity = await client.get_input_entity(cmd.chat_id)
            except Exception:
                try:
                    entity = await client.get_entity(cmd.chat_id)
                except Exception:
                    entity = cmd.chat_id

            messages: list[Message] = []
            offset_id = cmd.before_id if cmd.before_id is not None else 0
            async for m in client.iter_messages(entity, limit=cmd.limit, offset_id=offset_id):
                if m:
                    try:
                        domain_m = map_telethon_message_to_domain(m, cmd.account_id, cmd.chat_id)
                        messages.append(domain_m)
                    except Exception as e:
                        logger.warning(f"Error mapping message {getattr(m, 'id', None)}: {e}")

            done["count"] = len(messages)
            if messages:
                chat = repo_get_chat(self.conn, cmd.account_id, cmd.chat_id)
                if not chat:
                    upsert_chats(
                        self.conn,
                        [Chat(account_id=cmd.account_id, chat_id=cmd.chat_id, title=f"Chat {cmd.chat_id}")],
                    )

                upsert_messages(self.conn, messages)

                if cmd.before_id is None:
                    newest = max(messages, key=lambda x: x.message_id)
                    chat = repo_get_chat(self.conn, cmd.account_id, cmd.chat_id)
                    if chat:
                        chat.last_message_id = newest.message_id
                        chat.last_date = newest.date
                        chat.last_preview = newest.plain_text or f"[{newest.kind.value}]"
                        upsert_chats(self.conn, [chat])
                    self._emit("chats_changed", {"user_id": cmd.account_id})

                self._emit("messages_changed", {
                    "user_id": cmd.account_id,
                    "chat_id": cmd.chat_id,
                    "count": len(messages),
                    "before_id": cmd.before_id,
                })
            elif cmd.before_id is not None:
                # An empty page is the beginning only after Telegram answered.
                # A local cache miss is not this event.
                done["exhausted"] = True
                self._emit("status", {
                    "message": "Beginning of history reached.",
                    "is_error": False,
                })
        except errors.FloodWaitError as e:
            done["error"] = f"Rate limited: wait {e.seconds}s"
            self._emit("status", {"message": done["error"], "is_error": True})
        except Exception as e:
            done["error"] = type(e).__name__
            logger.warning(f"Error fetching messages for chat {cmd.chat_id}: {type(e).__name__}: {e}")
        self._emit("fetch_messages_done", done)

    async def _handle_fetch_roles(self, cmd: FetchRolesCmd) -> None:
        """Load owner and admin ids for a group or channel."""
        client = self.clients.get(cmd.account_id)
        if not client:
            self._emit("roles_changed", {
                "user_id": cmd.account_id,
                "chat_id": cmd.chat_id,
                "roles": {},
                "error": True,
            })
            return
        try:
            roles = await self._load_member_roles(client, cmd.chat_id)
            self._emit("roles_changed", {
                "user_id": cmd.account_id,
                "chat_id": cmd.chat_id,
                "roles": roles,
            })
        except Exception as e:
            logger.warning(f"Error fetching roles for chat {cmd.chat_id}: {type(e).__name__}: {e}")
            self._emit("roles_changed", {
                "user_id": cmd.account_id,
                "chat_id": cmd.chat_id,
                "roles": {},
                "error": True,
            })

    async def _load_member_roles(self, client: TelegramClient, chat_id: int) -> dict[int, str]:
        from telethon.tl.types import ChannelParticipantsAdmins

        entity: Any = chat_id
        try:
            entity = await client.get_input_entity(chat_id)
        except Exception:
            try:
                entity = await client.get_entity(chat_id)
            except Exception:
                entity = chat_id

        try:
            users = await client.get_participants(
                entity, filter=ChannelParticipantsAdmins(), limit=200
            )
        except Exception:
            kind_name = type(entity).__name__
            # A failed admin filter on a channel must not page every member.
            if "Channel" in kind_name:
                raise
            users = await client.get_participants(entity, limit=200)

        roles: dict[int, str] = {}
        for user in users:
            role = member_role(getattr(user, "participant", None))
            user_id = getattr(user, "id", None)
            if role and user_id:
                roles[int(user_id)] = role
        return roles

    async def _client_for_send(self, account_id: int) -> TelegramClient | None:
        client = self.clients.get(account_id)
        if client is not None:
            return client
        await self._reconnect_account(account_id)
        return self.clients.get(account_id)

    async def _handle_send_message(self, cmd: SendMessageCmd) -> None:
        client = await self._client_for_send(cmd.account_id)
        if not client:
            with self.conn:
                self.conn.execute(
                    "UPDATE messages SET send_state = ? WHERE account_id = ? AND chat_id = ? AND message_id = ?",
                    (SendState.FAILED.value, cmd.account_id, cmd.chat_id, cmd.temp_msg_id),
                )
            self._emit("messages_changed", {"user_id": cmd.account_id, "chat_id": cmd.chat_id})
            self._emit("status", {"message": _NOT_CONNECTED, "is_error": True})
            return

        try:
            entity = cmd.chat_id
            try:
                entity = await client.get_input_entity(cmd.chat_id)
            except Exception:
                try:
                    entity = await client.get_entity(cmd.chat_id)
                except Exception:
                    entity = cmd.chat_id

            reply_to = cmd.reply_to_id if (cmd.reply_to_id and cmd.reply_to_id > 0) else None
            sent = await client.send_message(entity, cmd.text, reply_to=reply_to)

            # Physically remove optimistic queued placeholder
            with self.conn:
                self.conn.execute(
                    "DELETE FROM messages WHERE account_id = ? AND chat_id = ? AND message_id = ?",
                    (cmd.account_id, cmd.chat_id, cmd.temp_msg_id),
                )

            mapped = map_telethon_message_to_domain(sent, cmd.account_id, cmd.chat_id)
            upsert_messages(self.conn, [mapped])

            chat = repo_get_chat(self.conn, cmd.account_id, cmd.chat_id)
            if chat:
                chat.last_message_id = mapped.message_id
                chat.last_date = mapped.date
                chat.last_preview = mapped.plain_text
                upsert_chats(self.conn, [chat])

            self._emit("chats_changed", {"user_id": cmd.account_id})
            self._emit("messages_changed", {"user_id": cmd.account_id, "chat_id": cmd.chat_id})
        except Exception as e:
            logger.warning(f"Failed to send message: {type(e).__name__}: {e}")
            with self.conn:
                self.conn.execute(
                    "UPDATE messages SET send_state = ? WHERE account_id = ? AND chat_id = ? AND message_id = ?",
                    (SendState.FAILED.value, cmd.account_id, cmd.chat_id, cmd.temp_msg_id),
                )
            self._emit("messages_changed", {"user_id": cmd.account_id, "chat_id": cmd.chat_id})
            self._emit("status", {"message": f"Send failed: {type(e).__name__}", "is_error": True})

    async def _handle_send_files(self, cmd: SendFilesCmd) -> None:
        client = await self._client_for_send(cmd.account_id)
        if not client or not cmd.paths:
            logger.warning("Cannot send files for account %s: not connected", cmd.account_id)
            self._mark_file_send_failed(cmd)
            self._emit("status", {"message": _NOT_CONNECTED, "is_error": True})
            return

        done = 0
        try:
            entity = cmd.chat_id
            try:
                entity = await client.get_input_entity(cmd.chat_id)
            except Exception:
                try:
                    entity = await client.get_entity(cmd.chat_id)
                except Exception:
                    entity = cmd.chat_id

            reply_to = cmd.reply_to_id if (cmd.reply_to_id and cmd.reply_to_id > 0) else None
            groups = send_groups(cmd.paths, as_file=cmd.as_file)
            weights = upload_weights(cmd.paths)
            overall = sum(weights) or 1.0
            label = upload_label(cmd.paths, as_file=cmd.as_file)
            seen: dict[str, int] = {"pct": -1}
            completed = 0.0
            offset = 0
            self._emit_upload(label, 0.0, overall, seen)
            for group_index, group in enumerate(groups):
                ids = cmd.temp_ids[done:done + len(group)]
                caption = cmd.caption if group_index == 0 else ""
                reply = reply_to if group_index == 0 else None
                file_arg: Any = group if len(group) > 1 else group[0]
                group_weight = sum(weights[offset:offset + len(group)])
                sent = await self._send_file_once(
                    client,
                    entity,
                    file_arg,
                    caption=caption or None,
                    force_document=cmd.as_file,
                    reply_to=reply,
                    supports_streaming=not cmd.as_file,
                    progress_callback=self._upload_callback(
                        label, completed, group_weight, overall, seen
                    ),
                )
                completed += group_weight
                offset += len(group)
                sent_list = list(sent) if isinstance(sent, (list, tuple)) else [sent]
                sent_list = [item for item in sent_list if item is not None]
                if len(sent_list) != len(group):
                    raise RuntimeError("Telegram did not return the sent file")
                mapped = [
                    map_telethon_message_to_domain(item, cmd.account_id, cmd.chat_id)
                    for item in sent_list
                ]
                self._swap_sent_files(cmd, ids, mapped)
                done += len(group)
        except Exception as e:
            logger.warning(f"Failed to send files: {type(e).__name__}: {e}")
            if done < len(cmd.temp_ids):
                self._mark_file_send_failed(
                    SendFilesCmd(
                        account_id=cmd.account_id,
                        chat_id=cmd.chat_id,
                        temp_ids=cmd.temp_ids[done:],
                        paths=cmd.paths[done:],
                        as_file=cmd.as_file,
                        caption=cmd.caption,
                        reply_to_id=cmd.reply_to_id,
                    )
                )
            detail = f"{type(e).__name__}: {e}"
            if len(detail) > 120:
                detail = detail[:117] + "..."
            self._emit("chats_changed", {"user_id": cmd.account_id})
            self._emit("status", {"message": f"Send failed: {detail}", "is_error": True})
            return

        self._emit_upload(label, overall, overall, seen)
        self._emit("chats_changed", {"user_id": cmd.account_id})
        self._emit("messages_changed", {"user_id": cmd.account_id, "chat_id": cmd.chat_id})

    def _upload_callback(
        self,
        label: str,
        base: float,
        group_weight: float,
        overall: float,
        seen: dict[str, int],
    ) -> Callable[[float, float], None]:
        """Map one Telethon progress pair onto the whole send.

        A single file reports bytes. An album reports a file fraction.
        Both are a 0..1 share of this group, then a share of the send.
        """

        def callback(current: float, total: float) -> None:
            if total:
                frac = max(0.0, min(1.0, float(current) / float(total)))
            else:
                frac = 1.0 if current else 0.0
            self._emit_upload(label, base + frac * group_weight, overall, seen)

        return callback

    def _emit_upload(self, label: str, current: float, total: float, seen: dict[str, int]) -> None:
        total = total or 1.0
        pct = int(max(0.0, min(1.0, current / total)) * 100)
        if pct == seen.get("pct"):
            return
        seen["pct"] = pct
        self._emit(
            "status",
            {"message": upload_progress_line(label, current, total), "is_error": False},
        )

    async def _send_file_once(self, client: TelegramClient, entity: Any, file_arg: Any, **kwargs: Any) -> Any:
        """Send once, and try once more when the session file is briefly locked."""
        try:
            return await client.send_file(entity, file_arg, parse_mode=None, **kwargs)
        except Exception as exc:
            if "database is locked" not in str(exc).lower():
                raise
            logger.warning("Session file was locked. Retrying the file send.")
            await asyncio.sleep(0.5)
            return await client.send_file(entity, file_arg, parse_mode=None, **kwargs)

    def _swap_sent_files(self, cmd: SendFilesCmd, temp_ids: Sequence[int], mapped: list[Message]) -> None:
        """Replace optimistic rows with the messages Telegram returned."""
        if temp_ids:
            placeholders = ",".join("?" for _ in temp_ids)
            with self.conn:
                self.conn.execute(
                    f"DELETE FROM messages WHERE account_id = ? AND chat_id = ? AND message_id IN ({placeholders})",
                    (cmd.account_id, cmd.chat_id, *temp_ids),
                )
        if not mapped:
            return
        upsert_messages(self.conn, mapped)
        chat = repo_get_chat(self.conn, cmd.account_id, cmd.chat_id)
        if chat:
            last = mapped[-1]
            chat.last_message_id = last.message_id
            chat.last_date = last.date
            preview = last.plain_text or last.caption or f"[{last.kind.value}]"
            chat.last_preview = preview
            upsert_chats(self.conn, [chat])

    def _mark_file_send_failed(self, cmd: SendFilesCmd) -> None:
        with self.conn:
            for temp_id in cmd.temp_ids:
                self.conn.execute(
                    "UPDATE messages SET send_state = ? WHERE account_id = ? AND chat_id = ? AND message_id = ?",
                    (SendState.FAILED.value, cmd.account_id, cmd.chat_id, temp_id),
                )
        self._emit("messages_changed", {"user_id": cmd.account_id, "chat_id": cmd.chat_id})

    async def _handle_edit_message(self, cmd: EditMessageCmd) -> None:
        client = self.clients.get(cmd.account_id)
        if not client:
            return
        try:
            entity = cmd.chat_id
            try:
                entity = await client.get_input_entity(cmd.chat_id)
            except Exception:
                try:
                    entity = await client.get_entity(cmd.chat_id)
                except Exception:
                    entity = cmd.chat_id

            edited = await client.edit_message(entity, cmd.message_id, cmd.text)
            if edited:
                mapped = map_telethon_message_to_domain(edited, cmd.account_id, cmd.chat_id)
                upsert_messages(self.conn, [mapped])
                self._emit("messages_changed", {"user_id": cmd.account_id, "chat_id": cmd.chat_id})
        except Exception as e:
            logger.warning(f"Failed to edit message: {type(e).__name__}: {e}")
            self._emit("status", {"message": f"Edit failed: {type(e).__name__}", "is_error": True})

    async def _handle_delete_messages(self, cmd: DeleteMessagesCmd) -> None:
        client = self.clients.get(cmd.account_id)
        if not client:
            return
        try:
            entity = cmd.chat_id
            try:
                entity = await client.get_input_entity(cmd.chat_id)
            except Exception:
                try:
                    entity = await client.get_entity(cmd.chat_id)
                except Exception:
                    entity = cmd.chat_id

            await client.delete_messages(entity, list(cmd.message_ids))
            delete_messages(self.conn, cmd.account_id, cmd.chat_id, cmd.message_ids)
            self._emit("messages_changed", {"user_id": cmd.account_id, "chat_id": cmd.chat_id})
        except Exception as e:
            logger.warning(f"Failed to delete messages: {type(e).__name__}: {e}")
            self._emit("status", {"message": f"Delete failed: {type(e).__name__}", "is_error": True})

    async def _handle_mark_chat_read(self, cmd: MarkChatReadCmd) -> None:
        client = self.clients.get(cmd.account_id)
        if not client:
            return
        try:
            for cid in cmd.chat_ids:
                try:
                    entity = await client.get_input_entity(cid)
                except Exception:
                    entity = cid
                await client.send_read_acknowledge(entity)
        except Exception as e:
            logger.warning(f"Failed to acknowledge read for chat: {type(e).__name__}: {e}")

    async def _handle_archive_chats(self, cmd: ArchiveChatsCmd) -> bool:
        """Move peers into Telegram's archive folder, or back to the main list."""
        client = self.clients.get(cmd.account_id)
        folder = 1 if cmd.archived else 0
        if not client:
            self._revert_archive(cmd)
            self._emit("status", {"message": "Archive failed: not connected", "is_error": True})
            return False
        failed: list[int] = []
        for start in range(0, len(cmd.chat_ids), _FOLDER_BATCH):
            chunk = cmd.chat_ids[start:start + _FOLDER_BATCH]
            try:
                await client.edit_folder(chunk, folder)
            except Exception as e:
                logger.warning(f"Failed to archive chats: {type(e).__name__}: {e}")
                failed.extend(chunk)
                self._emit("status", {"message": f"Archive failed: {type(e).__name__}", "is_error": True})
        if failed:
            repo_archive_chats(self.conn, cmd.account_id, failed, archived=not cmd.archived)
            self._emit("chats_changed", {"user_id": cmd.account_id})
        return not failed

    def _revert_archive(self, cmd: ArchiveChatsCmd) -> None:
        repo_archive_chats(self.conn, cmd.account_id, cmd.chat_ids, archived=not cmd.archived)
        self._emit("chats_changed", {"user_id": cmd.account_id})

    async def _handle_mute_chats(self, cmd: MuteChatsCmd) -> bool:
        """Mute or unmute peers for every Telegram app on this account."""
        from telethon.tl.functions.account import UpdateNotifySettingsRequest
        from telethon.tl.types import InputNotifyPeer, InputPeerNotifySettings

        client = self.clients.get(cmd.account_id)
        if not client:
            repo_mute_chats(self.conn, cmd.account_id, cmd.chat_ids, muted=not cmd.muted)
            self._emit("chats_changed", {"user_id": cmd.account_id})
            self._emit("status", {"message": "Mute failed: not connected", "is_error": True})
            return False
        until = datetime.fromtimestamp(
            MUTE_UNTIL_FOREVER if cmd.muted else 0,
            tz=timezone.utc,
        )
        failed: list[int] = []
        for cid in cmd.chat_ids:
            try:
                entity = await client.get_input_entity(cid)
                await client(UpdateNotifySettingsRequest(
                    peer=InputNotifyPeer(entity),
                    settings=InputPeerNotifySettings(mute_until=until),
                ))
            except Exception as e:
                logger.warning(f"Failed to mute chat {cid}: {type(e).__name__}: {e}")
                failed.append(cid)
        if failed:
            repo_mute_chats(self.conn, cmd.account_id, failed, muted=not cmd.muted)
            self._emit("chats_changed", {"user_id": cmd.account_id})
            self._emit("status", {"message": "Mute failed", "is_error": True})
        return not failed

    async def _push_saved_dialog_state(self, user_id: int) -> None:
        """Send archive and mute flags saved before those actions reached Telegram.

        Runs once per account, before the first dialog sync, so that sync
        does not copy Telegram's old state over this install's list.
        """
        if not dialog_state_needs_push(self.conn, user_id):
            return
        archived, muted = saved_archive_and_mute_ids(self.conn, user_id)
        if not archived and not muted:
            mark_dialog_state_pushed(self.conn, user_id)
            return
        self._emit("status", {"message": "saving archive and mute...", "is_error": False})
        ok = True
        if archived:
            ok = await self._handle_archive_chats(
                ArchiveChatsCmd(user_id, archived, True)
            ) and ok
        if muted:
            ok = await self._handle_mute_chats(
                MuteChatsCmd(user_id, muted, True)
            ) and ok
        if ok:
            mark_dialog_state_pushed(self.conn, user_id)

    async def _flush_pending_deletes(self, force: bool = False) -> None:
        """Send deletes whose undo window has closed.

        Shutdown passes force=True so a quit inside the undo window still
        reaches Telegram. Undo removes the row before that.
        """
        claimed = claim_due_chat_deletes(
            self.conn,
            limit=_DELETE_BATCH if not force else 500,
            force=force,
        )
        for account_id, chat_id, attempts in claimed:
            client = self.clients.get(account_id)
            if client is None:
                reschedule_chat_delete(self.conn, account_id, chat_id, attempts, delay=1.0)
                continue
            self._deletes_in_flight.add((account_id, chat_id))
            try:
                await client.delete_dialog(chat_id, revoke=False)
            except Exception as e:
                logger.warning(f"Failed to delete dialog {chat_id}: {type(e).__name__}: {e}")
                nxt = attempts + 1
                if nxt >= _DELETE_ATTEMPTS:
                    self._emit(
                        "status",
                        {"message": f"Delete failed: {type(e).__name__}", "is_error": True},
                    )
                else:
                    reschedule_chat_delete(self.conn, account_id, chat_id, nxt)
            else:
                repo_delete_chats(self.conn, account_id, [chat_id])
                self._emit("chats_changed", {"user_id": account_id})
            finally:
                self._deletes_in_flight.discard((account_id, chat_id))

    async def _finish_login(self, user: Any) -> None:
        user_id = int(getattr(user, "id", 0))
        final_session = get_session_path(user_id)

        # Move/save to final session path
        if self._login_client:
            await self._login_client.disconnect()
            if hasattr(self._login_client.session, "close"):
                try:
                    self._login_client.session.close()
                except Exception:
                    pass
            temp_session_file = get_session_path("temp_login").with_suffix(".session")
            final_session_file = final_session.with_suffix(".session")
            if temp_session_file.exists():
                if final_session_file.exists():
                    try:
                        final_session_file.unlink()
                    except Exception:
                        pass
                temp_session_file.rename(final_session_file)

        # Reopen with final session path and store
        self._api_id = self._login_api_id
        self._api_hash = self._login_api_hash
        client = create_telegram_client(final_session, self._login_api_id, self._login_api_hash)
        await client.connect()
        me = await client.get_me()
        account = map_telethon_user_to_account(me or user, str(final_session))
        upsert_account(self.conn, account)
        self.clients[account.user_id] = client
        self._attach_event_handlers(account.user_id, client)
        await self._push_saved_dialog_state(account.user_id)
        asyncio.create_task(self._sync_folders(account.user_id, client))
        asyncio.create_task(self._sync_dialogs(account.user_id, client))

        self._login_generation += 1
        self._cancel_login_resend_task()
        self._clear_login_code_watchers()
        if self._qr_task and not self._qr_task.done():
            self._qr_task.cancel()
        self._qr_task = None
        self._qr_login = None
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

        @client.on(events.Raw)
        async def on_dialog_state(update: Any) -> None:
            try:
                from telethon import utils
                from telethon.tl.types import NotifyPeer, UpdateFolderPeers, UpdateNotifySettings

                if isinstance(update, UpdateFolderPeers):
                    changed = False
                    for item in update.folder_peers or []:
                        chat_id = utils.get_peer_id(item.peer)
                        chat = repo_get_chat(self.conn, user_id, chat_id)
                        if chat is None:
                            continue
                        archived = int(item.folder_id) == 1
                        if chat.archived != archived:
                            repo_archive_chats(self.conn, user_id, [chat_id], archived)
                            changed = True
                    if changed:
                        self._emit("chats_changed", {"user_id": user_id})
                elif isinstance(update, UpdateNotifySettings) and isinstance(update.peer, NotifyPeer):
                    chat_id = utils.get_peer_id(update.peer.peer)
                    chat = repo_get_chat(self.conn, user_id, chat_id)
                    if chat is None:
                        return
                    muted = notify_is_muted(update.notify_settings)
                    if chat.muted != muted:
                        repo_mute_chats(self.conn, user_id, [chat_id], muted)
                        self._emit("chats_changed", {"user_id": user_id})
            except Exception as e:
                logger.warning(f"Error handling dialog state: {type(e).__name__}")

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
        """Load the archive folder before publishing the main list.

        One combined dialog walk is ordered by date, and Telegram often
        leaves folder_id off those dialogs. Archived chats are then stored
        on the main list and drop off a page at a time. The archive folder
        is written in full first, and the screen hears about it once.
        """
        self._emit("status", {"message": "syncing dialogs...", "is_error": False})
        try:
            await self._sync_dialog_folder(user_id, client, archived=True)
            await self._sync_dialog_folder(user_id, client, archived=False)
            self._emit("status", {"message": "ready", "is_error": False})
        except errors.FloodWaitError as e:
            self._emit("status", {"message": f"rate limited {e.seconds}s", "is_error": True})
        except Exception as e:
            logger.warning(f"Error syncing dialogs for {user_id}: {type(e).__name__}")
            self._emit("status", {"message": f"sync error: {type(e).__name__}", "is_error": True})

    async def _sync_dialog_folder(
        self,
        user_id: int,
        client: TelegramClient,
        *,
        archived: bool,
    ) -> None:
        """Upsert one peer folder. Archive pages stay quiet until the folder is done."""
        batch: list[Chat] = []
        msg_batch: list[Message] = []
        pending = pending_delete_ids(self.conn, user_id)
        async for dialog in client.iter_dialogs(limit=None, archived=archived):
            chat = map_telethon_dialog_to_chat(dialog, user_id)
            # The request decides the archive bit. A missing folder_id is
            # not "main list": Telethon only sets Dialog.archived when the
            # field is present, and the combined dialog list omits it.
            chat.archived = archived
            if chat.chat_id in pending or (user_id, chat.chat_id) in self._deletes_in_flight:
                continue
            folder_id = getattr(dialog, "folder_id", 0)
            if folder_id:
                chat.folder_ids.add(folder_id)
            batch.append(chat)

            if dialog.message:
                try:
                    msg_batch.append(
                        map_telethon_message_to_domain(dialog.message, user_id, chat.chat_id)
                    )
                except Exception:
                    pass

            if len(batch) >= 100:
                self._write_dialog_page(user_id, batch, msg_batch, notify=not archived)
                batch = []
                msg_batch = []
                pending = pending_delete_ids(self.conn, user_id)
                await asyncio.sleep(0.01)

        if batch:
            self._write_dialog_page(user_id, batch, msg_batch, notify=not archived)
        if archived:
            self._emit("chats_changed", {"user_id": user_id})

    def _write_dialog_page(
        self,
        user_id: int,
        batch: list[Chat],
        msg_batch: list[Message],
        *,
        notify: bool,
    ) -> None:
        upsert_chats(self.conn, batch)
        if msg_batch:
            upsert_messages(self.conn, msg_batch)
        if notify:
            self._emit("chats_changed", {"user_id": user_id, "count": len(batch)})

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
        self._login_generation += 1
        self._login_auto_resends = 0
        self._login_resend_not_before = 0.0
        self._login_can_resend = True
        self._cancel_login_resend_task()
        self._clear_login_code_watchers()
        if self._qr_task and not self._qr_task.done():
            self._qr_task.cancel()
        self._qr_task = None
        self._qr_login = None
        if self._login_client:
            try:
                await self._login_client.disconnect()
                if hasattr(self._login_client.session, "close"):
                    self._login_client.session.close()
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
        await self._flush_pending_deletes(force=True)
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
