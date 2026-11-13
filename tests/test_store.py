"""Tests for SQLite database schema and repo operations."""

import sqlite3
import time
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
    cancel_chat_deletes,
    cancel_message_deletes,
    claim_due_chat_deletes,
    claim_due_message_deletes,
    finish_message_deletes,
    min_message_id,
    reschedule_message_deletes,
    schedule_message_deletes,
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
    pending_delete_ids,
    save_draft,
    schedule_chat_deletes,
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


def test_pending_chat_delete_waits_for_undo(conn):
    schedule_chat_deletes(conn, account_id=1, chat_ids=[10, 20])
    assert pending_delete_ids(conn, 1) == {10, 20}
    assert claim_due_chat_deletes(conn) == []
    assert pending_delete_ids(conn, 1) == {10, 20}

    cancel_chat_deletes(conn, account_id=1, chat_ids=[10])
    assert pending_delete_ids(conn, 1) == {20}

    claimed = claim_due_chat_deletes(conn, now=time.time() + 30)
    assert claimed == [(1, 20, 0)]
    assert pending_delete_ids(conn, 1) == set()

    schedule_chat_deletes(conn, account_id=1, chat_ids=[20], delay=30)
    forced = claim_due_chat_deletes(conn, force=True)
    assert forced == [(1, 20, 0)]
    assert pending_delete_ids(conn, 1) == set()


def _store_messages(conn, ids):
    upsert_account(conn, Account(user_id=1, phone="+1000"))
    upsert_chats(conn, [Chat(account_id=1, chat_id=5, title="Notes")])
    upsert_messages(conn, [
        Message(account_id=1, chat_id=5, message_id=mid, plain_text=f"m{mid}")
        for mid in ids
    ])


def _visible_ids(conn):
    return [m.message_id for m in get_messages(conn, 1, 5)]


def test_pending_message_delete_hides_until_undo(conn):
    _store_messages(conn, [1, 2, 3])
    schedule_message_deletes(conn, 1, 5, [2, 3], revoke=False)
    assert _visible_ids(conn) == [1]
    assert claim_due_message_deletes(conn) == []

    cancel_message_deletes(conn, 1, 5, [3])
    assert _visible_ids(conn) == [1, 3]


def test_claimed_message_delete_stays_hidden_until_finished(conn):
    _store_messages(conn, [1, 2, 3])
    schedule_message_deletes(conn, 1, 5, [2], revoke=True)
    schedule_message_deletes(conn, 1, 5, [3], revoke=False)

    claimed = claim_due_message_deletes(conn, now=time.time() + 30)
    assert sorted(claimed) == [(1, 5, False, 0, [3]), (1, 5, True, 0, [2])]
    # Leased, not dropped: the rows stay hidden while Telegram answers.
    assert _visible_ids(conn) == [1]
    assert claim_due_message_deletes(conn, now=time.time() + 31) == []

    finish_message_deletes(conn, 1, 5, [2])
    assert _visible_ids(conn) == [1]
    cancel_message_deletes(conn, 1, 5, [3])
    assert _visible_ids(conn) == [1, 3]


def test_failed_message_delete_is_retried_later(conn):
    _store_messages(conn, [1])
    schedule_message_deletes(conn, 1, 5, [1], revoke=False)
    claim_due_message_deletes(conn, now=time.time() + 30)
    reschedule_message_deletes(conn, 1, 5, [1], attempts=1, delay=0)
    assert claim_due_message_deletes(conn, now=time.time() + 1) == [(1, 5, False, 1, [1])]


def test_forced_claim_takes_deletes_still_in_their_window(conn):
    _store_messages(conn, [1])
    schedule_message_deletes(conn, 1, 5, [1], revoke=False, delay=30)
    assert claim_due_message_deletes(conn, force=True) == [(1, 5, False, 0, [1])]


def test_local_placeholders_are_the_newest_messages(conn):
    _store_messages(conn, list(range(1, 61)) + [-1, -2])
    latest = get_messages(conn, 1, 5, limit=50)
    assert [m.message_id for m in latest[-3:]] == [60, -1, -2]
    assert latest[0].message_id == 11
    older = get_messages(conn, 1, 5, limit=50, before_id=11)
    assert [m.message_id for m in older] == list(range(1, 11))
    assert get_messages(conn, 1, 5, before_id=-1) == []


def test_min_message_id_sees_local_placeholders(conn):
    assert min_message_id(conn) == 0
    _store_messages(conn, [-4, 7])
    assert min_message_id(conn) == -4


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
