"""Tests for TelethonGateway and worker lifecycle."""

import time
from datetime import datetime, timezone
import pytest
from tg_cli.domain.models import Account, AuthState, Chat, MessageKind, SendState
from tg_cli.store.repo import get_accounts, get_messages, upsert_account, upsert_chats
from tg_cli.telegram.telethon_gw import TelethonGateway
from tg_cli.telegram.worker import FetchMessagesCmd


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

