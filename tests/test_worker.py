"""Tests for TelethonGateway and worker lifecycle."""

import time
import pytest
from tg_cli.domain.models import Account, AuthState, SendState
from tg_cli.store.repo import get_accounts, get_messages, upsert_account
from tg_cli.telegram.telethon_gw import TelethonGateway


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
        from tg_cli.domain.models import Chat
        from tg_cli.store.repo import upsert_chats
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
