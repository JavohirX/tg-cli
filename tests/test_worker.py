"""Tests for TelethonGateway and worker lifecycle."""

import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from tg_cli.domain.files import send_groups
from tg_cli.domain.models import Account, AuthState, Chat, MessageKind, SendState
from tg_cli.store.repo import (
    get_accounts,
    get_chats,
    get_messages,
    pending_delete_ids,
    upsert_account,
    upsert_chats,
)
from tg_cli.telegram.telethon_gw import TelethonGateway
from tg_cli.telegram.worker import MUTE_UNTIL_FOREVER, FetchMessagesCmd


class MockTelethonMessage:
    def __init__(self, msg_id: int, text: str, date: datetime | None = None, out: bool = False):
        self.id = msg_id
        self.message = text
        self.date = date or datetime.now(timezone.utc)
        self.sender_id = 888 if out else 999
        self.sender = None
        self.out = out
        self.media = None
        self.reply_to = None
        self.fwd_from = None
        self.edit_date = None


class MockTelethonClient:
    def __init__(self, messages: list[MockTelethonMessage] | None = None):
        self.messages = messages or []
        self.sent_messages: list[tuple] = []
        self.sent_file_calls: list[dict] = []
        self.progress_points: list[tuple[float, float]] | None = None
        self.fail_file_sends = 0
        self.folder_edits: list[dict] = []
        self.deleted_dialogs: list[dict] = []
        self.requests: list[object] = []
        self.dialogs: list[object] = []
        self.archive_dialogs: list[object] | None = None
        self.main_dialogs: list[object] | None = None
        self.dialog_calls: list[bool | None] = []
        self.archive_iter_done = False

    async def get_input_entity(self, peer):
        return peer

    async def get_entity(self, peer):
        return peer

    async def iter_messages(self, entity, limit=50, offset_id=0):
        count = 0
        for m in self.messages:
            if offset_id and m.id >= offset_id:
                continue
            yield m
            count += 1
            if count >= limit:
                break

    async def send_file(self, entity, file, caption=None, force_document=False, reply_to=None, **kwargs):
        if self.fail_file_sends:
            self.fail_file_sends -= 1
            raise RuntimeError("upload failed")
        paths = list(file) if isinstance(file, (list, tuple)) else [file]
        callback = kwargs.get("progress_callback")
        self.sent_file_calls.append({
            "paths": paths,
            "caption": caption,
            "force_document": force_document,
            "reply_to": reply_to,
            "parse_mode": kwargs.get("parse_mode", "missing"),
            "supports_streaming": kwargs.get("supports_streaming"),
            "progress_callback": callback,
        })
        if callback is not None and self.progress_points:
            for current, total in self.progress_points:
                callback(current, total)
        created = []
        for index, _path in enumerate(paths):
            created.append(
                MockTelethonMessage(
                    msg_id=81000 + len(self.sent_file_calls) * 10 + index,
                    text=caption or "",
                    out=True,
                )
            )
        if isinstance(file, (list, tuple)):
            return created
        return created[0]

    async def send_message(self, entity, text, reply_to=None):
        new_id = 70001 + len(self.sent_messages)
        m = MockTelethonMessage(msg_id=new_id, text=text, out=True)
        self.sent_messages.append((entity, text, reply_to))
        return m

    async def edit_message(self, entity, message_id, text):
        m = MockTelethonMessage(msg_id=message_id, text=text, out=True)
        m.edit_date = datetime.now(timezone.utc)
        return m

    async def delete_messages(self, entity, message_ids):
        return len(message_ids)

    async def send_read_acknowledge(self, entity):
        return True

    async def edit_folder(self, entity, folder=None, *, unpack=None):
        ids = list(entity) if isinstance(entity, (list, tuple)) else [entity]
        self.folder_edits.append({"ids": ids, "folder": folder, "unpack": unpack})

    async def delete_dialog(self, entity, *, revoke=False):
        self.deleted_dialogs.append({"entity": entity, "revoke": revoke})

    async def __call__(self, request):
        self.requests.append(request)
        return True

    async def iter_dialogs(self, limit=None, **kwargs):
        archived = kwargs.get("archived")
        self.dialog_calls.append(archived)
        if archived is True and self.archive_dialogs is not None:
            source = self.archive_dialogs
        elif archived is False and self.main_dialogs is not None:
            source = self.main_dialogs
        else:
            source = self.dialogs
            if archived is True:
                source = [d for d in source if _dialog_is_archived(d)]
            elif archived is False:
                source = [d for d in source if not _dialog_is_archived(d)]
        for dialog in source:
            yield dialog
        if archived is True:
            self.archive_iter_done = True


def _dialog_is_archived(dialog: object) -> bool:
    return bool(getattr(dialog, "archived", False) or getattr(dialog, "folder_id", None) == 1)


def _bare_dialog(chat_id: int, name: str) -> SimpleNamespace:
    """A dialog whose folder_id Telegram left empty."""
    return SimpleNamespace(
        id=chat_id,
        name=name,
        unread_count=0,
        pinned=False,
        archived=False,
        folder_id=None,
        entity=None,
        notify_settings=None,
        message=None,
    )


def test_gateway_lifecycle_and_optimistic_send(tmp_path):
    db_file = tmp_path / "gw_test.db"
    events_received: list[tuple[str, dict]] = []

    def on_event(evt: str, data: dict):
        events_received.append((evt, data))

    gw = TelethonGateway(event_callback=on_event, db_path=db_file)
    gw.start()

    try:
        # Initial accounts empty
        assert len(gw.get_accounts()) == 0

        # Add account directly to db
        acc = Account(
            user_id=888,
            phone="+12025550188",
            username="botuser",
            display_name="Bot User",
            auth_state=AuthState.OK,
        )
        upsert_account(gw.conn, acc)
        assert len(gw.get_accounts()) == 1

        # Create chat before sending message to satisfy foreign key
        chat = Chat(account_id=888, chat_id=10, title="Test Chat")
        upsert_chats(gw.conn, [chat])

        # Optimistic send creates message with negative ID and QUEUED state
        sent = gw.send_message(account_id=888, chat_id=10, text="Optimistic hello")
        assert sent.message_id < 0
        assert sent.send_state == SendState.QUEUED
        assert sent.plain_text == "Optimistic hello"

        # Verify message persisted in SQLite
        db_msgs = gw.get_messages(account_id=888, chat_id=10)
        assert len(db_msgs) == 1
        assert db_msgs[0].message_id == sent.message_id
        assert db_msgs[0].plain_text == "Optimistic hello"

    finally:
        gw.stop()


def test_worker_fetch_messages_ingestion(tmp_path):
    db_file = tmp_path / "gw_fetch_test.db"
    events_received: list[tuple[str, dict]] = []

    def on_event(evt: str, data: dict):
        events_received.append((evt, data))

    gw = TelethonGateway(event_callback=on_event, db_path=db_file)
    gw.start()

    try:
        acc = Account(
            user_id=777,
            phone="+12025550177",
            username="mockuser",
            display_name="Mock User",
            auth_state=AuthState.OK,
        )
        upsert_account(gw.conn, acc)
        chat = Chat(account_id=777, chat_id=20, title="Telegram Chat")
        upsert_chats(gw.conn, [chat])

        # Attach mock Telethon client to worker
        mock_msgs = [
            MockTelethonMessage(msg_id=101, text="First Telegram message"),
            MockTelethonMessage(msg_id=102, text="Second Telegram message"),
            MockTelethonMessage(msg_id=103, text="Third Telegram message"),
        ]
        mock_client = MockTelethonClient(mock_msgs)
        gw.worker.clients[777] = mock_client

        # Initial SQLite messages is empty
        assert len(gw.conn.execute("SELECT * FROM messages WHERE chat_id=20").fetchall()) == 0

        # Trigger fetch
        gw.fetch_messages(account_id=777, chat_id=20, limit=50)

        # Wait for fetch to complete in worker thread
        deadline = time.time() + 3.0
        fetched = False
        while time.time() < deadline:
            if any(e == "fetch_messages_done" for e, _ in events_received):
                fetched = True
                break
            time.sleep(0.05)

        assert fetched, "fetch_messages_done event not received in time"

        # Verify messages ingested into SQLite
        msgs = gw.get_messages(account_id=777, chat_id=20)
        assert len(msgs) == 3
        assert msgs[0].message_id == 101
        assert msgs[0].plain_text == "First Telegram message"
        assert msgs[2].message_id == 103
        assert msgs[2].plain_text == "Third Telegram message"

        # Verify messages_changed event emitted
        msg_changed_events = [d for e, d in events_received if e == "messages_changed"]
        assert any(d.get("chat_id") == 20 for d in msg_changed_events)

    finally:
        gw.stop()


def _wait_for(events: list[tuple[str, dict]], predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_worker_older_page_is_not_exhausted_until_telegram_is_empty(tmp_path):
    db_file = tmp_path / "gw_older_page.db"
    events_received: list[tuple[str, dict]] = []

    def on_event(evt: str, data: dict):
        events_received.append((evt, data))

    gw = TelethonGateway(event_callback=on_event, db_path=db_file)
    gw.start()
    try:
        upsert_account(gw.conn, Account(
            user_id=777,
            phone="+12025550177",
            username="mockuser",
            display_name="Mock User",
            auth_state=AuthState.OK,
        ))
        upsert_chats(gw.conn, [Chat(account_id=777, chat_id=20, title="Telegram Chat")])
        gw.worker.clients[777] = MockTelethonClient([
            MockTelethonMessage(msg_id=101, text="Oldest"),
            MockTelethonMessage(msg_id=102, text="Middle"),
            MockTelethonMessage(msg_id=103, text="Newest"),
        ])

        gw.fetch_messages(account_id=777, chat_id=20, limit=50, before_id=103)
        assert _wait_for(
            events_received,
            lambda: any(
                evt == "fetch_messages_done" and data.get("before_id") == 103
                for evt, data in events_received
            ),
        )
        done = [data for evt, data in events_received if evt == "fetch_messages_done"][-1]
        assert done["count"] == 2
        assert done["exhausted"] is False
        assert "error" not in done
        changed = [data for evt, data in events_received if evt == "messages_changed"][-1]
        assert changed["before_id"] == 103
        assert changed["count"] == 2
        assert not any(
            evt == "status" and data.get("message") == "Beginning of history reached."
            for evt, data in events_received
        )

        events_received.clear()
        gw.fetch_messages(account_id=777, chat_id=20, limit=50, before_id=101)
        assert _wait_for(
            events_received,
            lambda: any(evt == "fetch_messages_done" for evt, _ in events_received),
        )
        done = [data for evt, data in events_received if evt == "fetch_messages_done"][-1]
        assert done["before_id"] == 101
        assert done["count"] == 0
        assert done["exhausted"] is True
        assert any(
            evt == "status" and data.get("message") == "Beginning of history reached."
            for evt, data in events_received
        )
    finally:
        gw.stop()


def test_worker_send_and_edit_message_lifecycle(tmp_path):
    db_file = tmp_path / "gw_send_test.db"
    events_received: list[tuple[str, dict]] = []

    def on_event(evt: str, data: dict):
        events_received.append((evt, data))

    gw = TelethonGateway(event_callback=on_event, db_path=db_file)
    gw.start()

    try:
        acc = Account(
            user_id=777,
            phone="+12025550177",
            username="mockuser",
            display_name="Mock User",
            auth_state=AuthState.OK,
        )
        upsert_account(gw.conn, acc)
        chat = Chat(account_id=777, chat_id=30, title="Send Chat")
        upsert_chats(gw.conn, [chat])

        mock_client = MockTelethonClient()
        gw.worker.clients[777] = mock_client

        # Send message
        sent = gw.send_message(account_id=777, chat_id=30, text="Hello through worker")
        assert sent.message_id < 0

        # Wait for worker to send and replace temporary message
        deadline = time.time() + 3.0
        replaced = False
        while time.time() < deadline:
            msgs = gw.get_messages(account_id=777, chat_id=30)
            if msgs and all(m.message_id > 0 for m in msgs):
                replaced = True
                break
            time.sleep(0.05)

        assert replaced, "Worker did not replace optimistic message with real message ID"
        msgs = gw.get_messages(account_id=777, chat_id=30)
        assert len(msgs) == 1
        assert msgs[0].message_id == 70001
        assert msgs[0].plain_text == "Hello through worker"
        assert msgs[0].send_state == SendState.SENT

        # Edit message
        gw.edit_message(account_id=777, chat_id=30, message_id=70001, text="Edited hello")
        time.sleep(0.1)
        edited_msgs = gw.get_messages(account_id=777, chat_id=30)
        assert edited_msgs[0].plain_text == "Edited hello"
        assert edited_msgs[0].edited is True

    finally:
        gw.stop()


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_worker_send_files_groups_them_and_honors_the_document_flag(tmp_path):
    db_file = tmp_path / "gw_files.db"
    gw = TelethonGateway(db_path=db_file)
    gw.start()
    try:
        acc = Account(
            user_id=777,
            phone="+12025550177",
            username="mockuser",
            display_name="Mock User",
            auth_state=AuthState.OK,
        )
        upsert_account(gw.conn, acc)
        chat = Chat(account_id=777, chat_id=31, title="Files")
        upsert_chats(gw.conn, [chat])
        mock_client = MockTelethonClient()
        gw.worker.clients[777] = mock_client

        queued = gw.send_files(
            account_id=777,
            chat_id=31,
            paths=["a.jpg", "b.png", "c.pdf"],
            as_file=True,
            caption="notes",
            reply_to_id=5,
        )
        assert [m.message_id < 0 for m in queued] == [True, True, True]
        assert queued[0].kind == MessageKind.FILE
        assert queued[0].plain_text.startswith("a.jpg")
        assert queued[0].caption == "notes"
        assert queued[0].local_path == "a.jpg"
        assert queued[1].caption == ""
        assert queued[0].reply_id == 5
        assert queued[2].reply_id is None

        # Documents are not an album. Telegram rejects that request.
        assert _wait_until(lambda: len(mock_client.sent_file_calls) == 3)
        assert [call["paths"] for call in mock_client.sent_file_calls] == [
            ["a.jpg"],
            ["b.png"],
            ["c.pdf"],
        ]
        assert all(call["force_document"] is True for call in mock_client.sent_file_calls)
        assert all(call["parse_mode"] is None for call in mock_client.sent_file_calls)
        assert mock_client.sent_file_calls[0]["caption"] == "notes"
        assert mock_client.sent_file_calls[0]["reply_to"] == 5
        assert mock_client.sent_file_calls[1]["caption"] is None
        assert mock_client.sent_file_calls[1]["reply_to"] is None
        assert _wait_until(
            lambda: all(m.message_id > 0 for m in gw.get_messages(777, 31))
        )
        assert len(gw.get_messages(777, 31)) == 3

        gw.send_files(
            account_id=777,
            chat_id=31,
            paths=["only.mp4"],
            as_file=False,
        )
        assert _wait_until(lambda: len(mock_client.sent_file_calls) == 4)
        single = mock_client.sent_file_calls[3]
        assert single["paths"] == ["only.mp4"]
        assert single["force_document"] is False
        assert single["reply_to"] is None
        assert single["supports_streaming"] is True
    finally:
        gw.stop()


def test_send_groups_keep_photos_together_and_documents_apart():
    photos = [f"{index}.jpg" for index in range(11)]
    assert [len(group) for group in send_groups(photos, as_file=False)] == [10, 1]
    assert send_groups(["a.jpg", "b.png", "c.webp"], as_file=False) == [
        ["a.jpg", "b.png"],
        ["c.webp"],
    ]
    assert send_groups(["a.pdf", "b.zip"], as_file=True) == [["a.pdf"], ["b.zip"]]


def test_photos_are_one_album(tmp_path):
    db_file = tmp_path / "gw_album.db"
    gw = TelethonGateway(db_path=db_file)
    gw.start()
    try:
        upsert_account(gw.conn, Account(user_id=777, phone="+1", display_name="Mock"))
        upsert_chats(gw.conn, [Chat(account_id=777, chat_id=31, title="Files")])
        mock_client = MockTelethonClient()
        gw.worker.clients[777] = mock_client
        gw.send_files(
            account_id=777,
            chat_id=31,
            paths=["a.jpg", "b.png"],
            as_file=False,
            caption="trip",
        )
        assert _wait_until(lambda: len(mock_client.sent_file_calls) == 1)
        call = mock_client.sent_file_calls[0]
        assert call["paths"] == ["a.jpg", "b.png"]
        assert call["force_document"] is False
        assert call["caption"] == "trip"
        assert call["parse_mode"] is None
        assert _wait_until(lambda: len(gw.get_messages(777, 31)) == 2)
        assert all(m.message_id > 0 for m in gw.get_messages(777, 31))
    finally:
        gw.stop()


def test_upload_progress_fills_across_files(tmp_path):
    db_file = tmp_path / "gw_progress.db"
    gw = TelethonGateway(db_path=db_file)
    gw.start()
    try:
        upsert_account(gw.conn, Account(user_id=777, phone="+1", display_name="Mock"))
        upsert_chats(gw.conn, [Chat(account_id=777, chat_id=31, title="Files")])
        small = tmp_path / "a.bin"
        large = tmp_path / "b.bin"
        small.write_bytes(b"x" * 100)
        large.write_bytes(b"y" * 300)
        mock_client = MockTelethonClient()
        mock_client.progress_points = [(0, 1), (0.5, 1), (1, 1)]
        gw.worker.clients[777] = mock_client
        statuses: list[str] = []

        def on_event(evt: str, data: dict) -> None:
            if evt == "status":
                statuses.append(str(data.get("message")))

        gw.worker.event_callback = on_event
        gw.send_files(
            account_id=777,
            chat_id=31,
            paths=[str(small), str(large)],
            as_file=True,
        )
        assert _wait_until(lambda: statuses and statuses[-1].endswith("100%"))
        assert statuses == [
            "Uploading 2 files: [░░░░░░░░░░] 0%",
            "Uploading 2 files: [█░░░░░░░░░] 12%",
            "Uploading 2 files: [██░░░░░░░░] 25%",
            "Uploading 2 files: [██████░░░░] 62%",
            "Uploading 2 files: [██████████] 100%",
        ]
    finally:
        gw.stop()


def test_file_send_fails_when_the_account_is_offline(tmp_path):
    db_file = tmp_path / "gw_offline.db"
    gw = TelethonGateway(db_path=db_file)
    gw.start()
    try:
        upsert_account(gw.conn, Account(user_id=777, phone="+1", display_name="Mock"))
        upsert_chats(gw.conn, [Chat(account_id=777, chat_id=31, title="Files")])
        photo = tmp_path / "shot.jpg"
        photo.write_bytes(b"jpg")
        queued = gw.send_files(account_id=777, chat_id=31, paths=[str(photo)], as_file=False)
        assert queued[0].local_path == str(photo)
        assert _wait_until(
            lambda: gw.get_message(777, 31, queued[0].message_id).send_state == SendState.FAILED
        )
        gw.conn.execute(
            "UPDATE messages SET local_path = ? WHERE message_id = ?",
            (str(tmp_path / "gone.jpg"), queued[0].message_id),
        )
        assert gw.retry_failed_message(777, 31, queued[0].message_id) is False
        assert gw.get_message(777, 31, queued[0].message_id).send_state == SendState.FAILED
    finally:
        gw.stop()


def test_retry_sends_a_failed_photo_again(tmp_path):
    db_file = tmp_path / "gw_retry.db"
    gw = TelethonGateway(db_path=db_file)
    gw.start()
    try:
        upsert_account(gw.conn, Account(user_id=777, phone="+1", display_name="Mock"))
        upsert_chats(gw.conn, [Chat(account_id=777, chat_id=31, title="Files")])
        photo = tmp_path / "shot.jpg"
        photo.write_bytes(b"jpg")
        mock_client = MockTelethonClient()
        mock_client.fail_file_sends = 1
        gw.worker.clients[777] = mock_client
        queued = gw.send_files(account_id=777, chat_id=31, paths=[str(photo)], as_file=False)
        assert _wait_until(
            lambda: gw.get_message(777, 31, queued[0].message_id).send_state == SendState.FAILED
        )
        assert gw.retry_failed_message(777, 31, queued[0].message_id) is True
        assert _wait_until(lambda: len(mock_client.sent_file_calls) == 1)
        assert mock_client.sent_file_calls[0]["paths"] == [str(photo)]
        assert _wait_until(
            lambda: gw.get_messages(777, 31)[0].message_id > 0
        )
    finally:
        gw.stop()


def test_archive_and_mute_are_sent_to_telegram(tmp_path):
    db_file = tmp_path / "gw_dialog_state.db"
    gw = TelethonGateway(db_path=db_file)
    gw.start()
    try:
        acc = Account(user_id=777, phone="+12025550177", display_name="Mock User")
        upsert_account(gw.conn, acc)
        upsert_chats(gw.conn, [
            Chat(account_id=777, chat_id=30, title="One"),
            Chat(account_id=777, chat_id=31, title="Two"),
        ])
        mock_client = MockTelethonClient()
        gw.worker.clients[777] = mock_client

        gw.archive_chats(777, [30, 31], archived=True)
        assert _wait_until(lambda: len(mock_client.folder_edits) == 1)
        assert mock_client.folder_edits[0]["ids"] == [30, 31]
        assert mock_client.folder_edits[0]["folder"] == 1
        assert gw.get_chat(777, 30).archived is True

        gw.archive_chats(777, [30], archived=False)
        assert _wait_until(lambda: len(mock_client.folder_edits) == 2)
        assert mock_client.folder_edits[1]["ids"] == [30]
        assert mock_client.folder_edits[1]["folder"] == 0

        gw.mute_chats(777, [31], muted=True)
        assert _wait_until(lambda: len(mock_client.requests) == 1)
        muted = mock_client.requests[0]
        assert isinstance(muted, UpdateNotifySettingsRequest)
        assert muted.settings.mute_until.timestamp() >= MUTE_UNTIL_FOREVER - 1
        assert gw.get_chat(777, 31).muted is True

        gw.mute_chats(777, [31], muted=False)
        assert _wait_until(lambda: len(mock_client.requests) == 2)
        assert mock_client.requests[1].settings.mute_until.timestamp() == 0
        assert gw.get_chat(777, 31).muted is False
    finally:
        gw.stop()


def test_archive_failure_restores_the_local_flag(tmp_path):
    db_file = tmp_path / "gw_archive_fail.db"
    gw = TelethonGateway(db_path=db_file)
    gw.start()
    try:
        acc = Account(user_id=777, phone="+12025550177", display_name="Mock User")
        upsert_account(gw.conn, acc)
        upsert_chats(gw.conn, [Chat(account_id=777, chat_id=30, title="One")])
        mock_client = MockTelethonClient()
        calls: list[int] = []

        async def reject_folder(entity, folder=None, *, unpack=None):
            calls.append(folder)
            raise RuntimeError("rejected")

        mock_client.edit_folder = reject_folder
        gw.worker.clients[777] = mock_client

        gw.archive_chats(777, [30], archived=True)
        assert _wait_until(lambda: calls == [1] and gw.get_chat(777, 30).archived is False)
    finally:
        gw.stop()


def test_delete_waits_for_undo_then_reaches_telegram(tmp_path):
    db_file = tmp_path / "gw_delete_dialog.db"
    gw = TelethonGateway(db_path=db_file)
    gw.start()
    mock_client = MockTelethonClient()
    try:
        acc = Account(user_id=777, phone="+12025550177", display_name="Mock User")
        upsert_account(gw.conn, acc)
        kept = Chat(account_id=777, chat_id=30, title="Undo me")
        gone = Chat(account_id=777, chat_id=31, title="Delete me")
        upsert_chats(gw.conn, [kept, gone])
        gw.worker.clients[777] = mock_client

        gw.delete_chats(777, [30])
        assert gw.get_chat(777, 30) is None
        assert pending_delete_ids(gw.conn, 777) == {30}
        time.sleep(0.2)
        assert mock_client.deleted_dialogs == []
        gw.restore_chats(777, [kept])
        assert gw.get_chat(777, 30) is not None
        assert pending_delete_ids(gw.conn, 777) == set()

        gw.delete_chats(777, [31])
        assert gw.get_chat(777, 31) is None
        gw.conn.execute("UPDATE pending_chat_deletes SET not_before = 0")
        assert _wait_until(lambda: len(mock_client.deleted_dialogs) == 1)
        assert mock_client.deleted_dialogs[0] == {"entity": 31, "revoke": False}
        assert pending_delete_ids(gw.conn, 777) == set()
    finally:
        gw.stop()
    assert mock_client.deleted_dialogs == [{"entity": 31, "revoke": False}]


def test_quit_sends_a_delete_that_was_still_undoable(tmp_path):
    db_file = tmp_path / "gw_delete_quit.db"
    gw = TelethonGateway(db_path=db_file)
    gw.start()
    mock_client = MockTelethonClient()
    try:
        acc = Account(user_id=777, phone="+12025550177", display_name="Mock User")
        upsert_account(gw.conn, acc)
        upsert_chats(gw.conn, [Chat(account_id=777, chat_id=31, title="Delete me")])
        gw.worker.clients[777] = mock_client
        gw.delete_chats(777, [31])
        assert mock_client.deleted_dialogs == []
    finally:
        gw.stop()
    assert mock_client.deleted_dialogs == [{"entity": 31, "revoke": False}]


def test_saved_archive_and_mute_are_pushed_once(tmp_path):
    db_file = tmp_path / "gw_push_state.db"
    gw = TelethonGateway(db_path=db_file)
    gw.start()
    try:
        acc = Account(user_id=777, phone="+12025550177", display_name="Mock User")
        upsert_account(gw.conn, acc)
        upsert_chats(gw.conn, [
            Chat(account_id=777, chat_id=30, title="Archived", archived=True),
            Chat(account_id=777, chat_id=31, title="Muted", muted=True),
            Chat(account_id=777, chat_id=32, title="Both", archived=True, muted=True),
        ])
        mock_client = MockTelethonClient()
        gw.worker.clients[777] = mock_client
        assert _wait_until(lambda: gw.worker.loop is not None)

        fut = asyncio.run_coroutine_threadsafe(
            gw.worker._push_saved_dialog_state(777),
            gw.worker.loop,
        )
        fut.result(timeout=3)
        assert mock_client.folder_edits[0]["ids"] == [30, 32]
        assert mock_client.folder_edits[0]["folder"] == 1
        peers = {req.peer.peer for req in mock_client.requests}
        assert peers == {31, 32}

        again = asyncio.run_coroutine_threadsafe(
            gw.worker._push_saved_dialog_state(777),
            gw.worker.loop,
        )
        again.result(timeout=3)
        assert len(mock_client.folder_edits) == 1
        assert len(mock_client.requests) == 2
    finally:
        gw.stop()


def test_dialog_sync_keeps_telegram_state_and_skips_pending_deletes(tmp_path):
    db_file = tmp_path / "gw_sync_state.db"
    gw = TelethonGateway(db_path=db_file)
    gw.start()
    try:
        acc = Account(user_id=777, phone="+12025550177", display_name="Mock User")
        upsert_account(gw.conn, acc)
        upsert_chats(gw.conn, [
            Chat(account_id=777, chat_id=30, title="Pending"),
            Chat(account_id=777, chat_id=31, title="Old title", archived=False, muted=False),
        ])
        mock_client = MockTelethonClient()
        future = datetime(2038, 1, 19, tzinfo=timezone.utc)
        mock_client.dialogs = [
            SimpleNamespace(
                id=30,
                name="Pending",
                unread_count=0,
                pinned=False,
                archived=False,
                folder_id=None,
                entity=None,
                notify_settings=None,
                message=None,
            ),
            SimpleNamespace(
                id=31,
                name="From Telegram",
                unread_count=2,
                pinned=False,
                archived=True,
                folder_id=1,
                entity=None,
                notify_settings=None,
                dialog=SimpleNamespace(
                    notify_settings=SimpleNamespace(mute_until=future),
                ),
                message=None,
            ),
        ]
        gw.worker.clients[777] = mock_client
        gw.delete_chats(777, [30])
        assert _wait_until(lambda: gw.worker.loop is not None)
        fut = asyncio.run_coroutine_threadsafe(
            gw.worker._sync_dialogs(777, mock_client),
            gw.worker.loop,
        )
        fut.result(timeout=3)
        assert gw.get_chat(777, 30) is None
        synced = gw.get_chat(777, 31)
        assert synced is not None
        assert synced.title == "From Telegram"
        assert synced.archived is True
        assert synced.muted is True
        assert synced.unread_count == 2
    finally:
        gw.stop()


def test_archive_folder_is_applied_before_the_main_list_refreshes(tmp_path):
    """Archived chats must not sit on the main list while later pages arrive.

    The dialog objects omit folder_id, which is how Telegram answers a
    combined dialog request. Membership comes from which folder was asked for.
    """
    db_file = tmp_path / "gw_archive_first.db"
    gw = TelethonGateway(db_path=db_file)
    gw.start()
    try:
        upsert_account(gw.conn, Account(user_id=777, phone="+12025550177", display_name="Mock User"))
        archive_ids = list(range(1, 102))
        upsert_chats(gw.conn, [
            Chat(account_id=777, chat_id=cid, title=f"Old {cid}")
            for cid in archive_ids
        ])
        upsert_chats(gw.conn, [Chat(account_id=777, chat_id=500, title="Inbox")])
        assert {c.chat_id for c in get_chats(gw.conn, account_id=777)} == set(archive_ids) | {500}

        mock_client = MockTelethonClient()
        mock_client.archive_dialogs = [_bare_dialog(cid, f"Archived {cid}") for cid in archive_ids]
        mock_client.main_dialogs = [_bare_dialog(500, "Inbox")]
        snapshots: list[tuple[bool, set[int]]] = []

        def on_event(evt: str, data: dict) -> None:
            if evt != "chats_changed" or data.get("user_id") != 777:
                return
            # The worker connection is the one this thread may read.
            main = {c.chat_id for c in get_chats(gw.worker.conn, account_id=777)}
            snapshots.append((mock_client.archive_iter_done, main))

        gw.worker.event_callback = on_event
        gw.worker.clients[777] = mock_client
        assert _wait_until(lambda: gw.worker.loop is not None)
        fut = asyncio.run_coroutine_threadsafe(
            gw.worker._sync_dialogs(777, mock_client),
            gw.worker.loop,
        )
        fut.result(timeout=3)

        assert mock_client.dialog_calls[:2] == [True, False]
        assert snapshots
        assert snapshots[0][0] is True
        assert snapshots[0][1] == {500}
        assert all(done and main == {500} for done, main in snapshots)
        archived = {c.chat_id for c in get_chats(gw.conn, account_id=777, folder_id=-2)}
        assert archived == set(archive_ids)
    finally:
        gw.stop()

