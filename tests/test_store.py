"""Tests for SQLite database schema and repo operations."""

import sqlite3
from datetime import datetime, timedelta, timezone
import pytest
from tg_cli.domain.models import (
    Account,
    AuthState,
    Chat,
    ChatKind,
    Draft,
    Folder,
    Message,
    MessageKind,
    SendState,
)
from tg_cli.store.db import init_db
from tg_cli.store.repo import (
    archive_chats,
    clear_draft,
    delete_account,
    delete_chats,
    delete_messages,
    get_accounts,
    get_chat,
    get_chats,
    get_draft,
    get_folders,
    get_messages,
    mark_chats_read,
    mute_chats,
    save_draft,
    upsert_account,
    upsert_chats,
    upsert_folders,
    upsert_messages,
)


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "test.db")
    yield c
    c.close()


def test_account_crud(conn):
    acc = Account(
        user_id=101,
        phone="+1234567890",
        username="testuser",
        display_name="Test User",
        session_path="sessions/101.session",
        auth_state=AuthState.OK,
    )
    upsert_account(conn, acc)

    accounts = get_accounts(conn)
    assert len(accounts) == 1
    assert accounts[0].user_id == 101
    assert accounts[0].username == "testuser"
    assert accounts[0].label == "@testuser"

    # Update account
    acc.display_name = "Updated User"
    upsert_account(conn, acc)
    updated = get_accounts(conn)[0]
    assert updated.display_name == "Updated User"

    # Delete
    delete_account(conn, 101)
    assert len(get_accounts(conn)) == 0


def test_folders_and_chats(conn):
    acc = Account(user_id=1, phone="+1000")
    upsert_account(conn, acc)

    folders = [
        Folder(account_id=1, folder_id=1, title="Personal", position=1),
        Folder(account_id=1, folder_id=2, title="Work", position=2),
    ]
    upsert_folders(conn, folders)
    assert len(get_folders(conn, 1)) == 2

    now = datetime.now(timezone.utc)
    chat1 = Chat(
        account_id=1,
        chat_id=10,
        title="Bob",
        kind=ChatKind.USER,
        unread_count=2,
        pinned=True,
        pin_rank=1,
        last_date=now - timedelta(minutes=5),
        last_preview="Hello",
    )
    chat2 = Chat(
        account_id=1,
        chat_id=20,
        title="Alice Team",
        kind=ChatKind.GROUP,
        unread_count=0,
        pinned=False,
        last_date=now - timedelta(minutes=1),
        last_preview="Meeting at 2",
    )
    upsert_chats(conn, [chat1, chat2])

    chats = get_chats(conn, account_id=1)
    assert len(chats) == 2
    # Pinned chat should come first despite earlier date
    assert chats[0].chat_id == 10
    assert chats[1].chat_id == 20

    # Filter query
    filtered = get_chats(conn, account_id=1, filter_query="alice")
    assert len(filtered) == 1
    assert filtered[0].chat_id == 20

    # Archive
    archive_chats(conn, account_id=1, chat_ids=[10], archived=True)
    # Main list excludes archive
    assert len(get_chats(conn, account_id=1)) == 1
    # Synthetic archive folder (-2)
    archived_list = get_chats(conn, account_id=1, folder_id=-2)
    assert len(archived_list) == 1
    assert archived_list[0].chat_id == 10

    # Mute & read
    mute_chats(conn, account_id=1, chat_ids=[20], muted=True)
    mark_chats_read(conn, account_id=1, chat_ids=[10], read=True)
    c10 = get_chat(conn, account_id=1, chat_id=10)
    assert c10.unread_count == 0
    c20 = get_chat(conn, account_id=1, chat_id=20)
    assert c20.muted is True


def test_messages_and_drafts(conn):
    acc = Account(user_id=1, phone="+1000")
    upsert_account(conn, acc)
    chat = Chat(account_id=1, chat_id=5, title="Notes")
    upsert_chats(conn, [chat])

    now = datetime.now(timezone.utc)
    m1 = Message(
        account_id=1,
        chat_id=5,
        message_id=1,
        plain_text="First message",
        date=now - timedelta(minutes=10),
    )
    m2 = Message(
        account_id=1,
        chat_id=5,
        message_id=2,
        plain_text="Second message",
        date=now - timedelta(minutes=5),
    )
    upsert_messages(conn, [m1, m2])

    messages = get_messages(conn, account_id=1, chat_id=5)
    assert len(messages) == 2
    # Chronological: m1 then m2
    assert messages[0].message_id == 1
    assert messages[1].message_id == 2

    # Draft test
    draft = Draft(account_id=1, chat_id=5, text="Draft text...", reply_to_id=2)
    save_draft(conn, draft)

    loaded_draft = get_draft(conn, account_id=1, chat_id=5)
    assert loaded_draft is not None
    assert loaded_draft.text == "Draft text..."
    assert loaded_draft.reply_to_id == 2

    clear_draft(conn, account_id=1, chat_id=5)
    assert get_draft(conn, account_id=1, chat_id=5) is None
