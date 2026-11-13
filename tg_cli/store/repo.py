"""Database repository providing typed CRUD queries for tg-cli domain models."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Sequence
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
    Transcript,
)


def _parse_iso(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None


def _format_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# Account operations
def get_accounts(conn: sqlite3.Connection) -> list[Account]:
    rows = conn.execute("SELECT * FROM accounts ORDER BY user_id ASC").fetchall()
    return [
        Account(
            user_id=r["user_id"],
            phone=r["phone"],
            username=r["username"] or "",
            display_name=r["display_name"] or "",
            session_path=r["session_path"] or "",
            auth_state=AuthState(r["auth_state"]),
        )
        for r in rows
    ]


def upsert_account(conn: sqlite3.Connection, account: Account) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO accounts (user_id, phone, username, display_name, session_path, auth_state)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                phone = excluded.phone,
                username = excluded.username,
                display_name = excluded.display_name,
                session_path = excluded.session_path,
                auth_state = excluded.auth_state
            """,
            (
                account.user_id,
                account.phone,
                account.username,
                account.display_name,
                account.session_path,
                account.auth_state.value,
            ),
        )


def delete_account(conn: sqlite3.Connection, user_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM accounts WHERE user_id = ?", (user_id,))


def get_account_unread_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """Return map of account_id -> total unread count across non-archived non-muted chats."""
    rows = conn.execute(
        """
        SELECT account_id, SUM(unread_count) as total_unread
        FROM chats
        WHERE archived = 0 AND muted = 0
        GROUP BY account_id
        """
    ).fetchall()
    return {r["account_id"]: int(r["total_unread"] or 0) for r in rows}



# Folder operations
def get_folders(conn: sqlite3.Connection, account_id: int) -> list[Folder]:
    rows = conn.execute(
        "SELECT * FROM folders WHERE account_id = ? ORDER BY position ASC", (account_id,)
    ).fetchall()
    return [
        Folder(
            account_id=r["account_id"],
            folder_id=r["folder_id"],
            title=r["title"],
            position=r["position"],
        )
        for r in rows
    ]


def upsert_folders(conn: sqlite3.Connection, folders: Sequence[Folder]) -> None:
    with conn:
        conn.executemany(
            """
            INSERT INTO folders (account_id, folder_id, title, position)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id, folder_id) DO UPDATE SET
                title = excluded.title,
                position = excluded.position
            """,
            [(f.account_id, f.folder_id, f.title, f.position) for f in folders],
        )


# Chat operations
def get_chats(
    conn: sqlite3.Connection,
    account_id: int,
    folder_id: int | None = None,
    filter_query: str = "",
) -> list[Chat]:
    params: list[object] = [account_id]

    sql = """
        SELECT c.*, GROUP_CONCAT(cf.folder_id) as folder_ids_str
        FROM chats c
        LEFT JOIN chat_folders cf ON c.account_id = cf.account_id AND c.chat_id = cf.chat_id
        WHERE c.account_id = ?
    """

    if folder_id is not None:
        if folder_id == -1:  # Synthetic Unread
            sql += " AND c.unread_count > 0 AND c.archived = 0"
        elif folder_id == -2:  # Synthetic Archive
            sql += " AND c.archived = 1"
        else:
            sql += " AND c.chat_id IN (SELECT chat_id FROM chat_folders WHERE account_id = ? AND folder_id = ?)"
            params.extend([account_id, folder_id])
    else:
        sql += " AND c.archived = 0"

    q = filter_query.strip().lower()
    if q:
        sql += " AND (LOWER(c.title) LIKE ? OR LOWER(c.last_preview) LIKE ?)"
        like_arg = f"%{q}%"
        params.extend([like_arg, like_arg])

    sql += " GROUP BY c.chat_id ORDER BY c.pinned DESC, c.pin_rank ASC, c.last_date DESC"

    rows = conn.execute(sql, params).fetchall()
    result: list[Chat] = []
    for r in rows:
        f_ids: set[int] = set()
        if r["folder_ids_str"]:
            f_ids = {int(x) for x in r["folder_ids_str"].split(",") if x}

        result.append(
            Chat(
                account_id=r["account_id"],
                chat_id=r["chat_id"],
                title=r["title"],
                kind=ChatKind(r["kind"]),
                unread_count=r["unread_count"],
                muted=bool(r["muted"]),
                pinned=bool(r["pinned"]),
                pin_rank=r["pin_rank"],
                archived=bool(r["archived"]),
                last_message_id=r["last_message_id"],
                last_date=_parse_iso(r["last_date"]),
                last_preview=r["last_preview"] or "",
                folder_ids=f_ids,
            )
        )
    return result


def get_chat(conn: sqlite3.Connection, account_id: int, chat_id: int) -> Chat | None:
    row = conn.execute(
        "SELECT * FROM chats WHERE account_id = ? AND chat_id = ?",
        (account_id, chat_id),
    ).fetchone()
    if not row:
        return None
    return Chat(
        account_id=row["account_id"],
        chat_id=row["chat_id"],
        title=row["title"],
        kind=ChatKind(row["kind"]),
        unread_count=row["unread_count"],
        muted=bool(row["muted"]),
        pinned=bool(row["pinned"]),
        pin_rank=row["pin_rank"],
        archived=bool(row["archived"]),
        last_message_id=row["last_message_id"],
        last_date=_parse_iso(row["last_date"]),
        last_preview=row["last_preview"] or "",
    )


def upsert_chats(conn: sqlite3.Connection, chats: Sequence[Chat]) -> None:
    with conn:
        conn.executemany(
            """
            INSERT INTO chats (
                account_id, chat_id, title, kind, unread_count, muted,
                pinned, pin_rank, archived, last_message_id, last_date, last_preview
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, chat_id) DO UPDATE SET
                title = excluded.title,
                kind = excluded.kind,
                unread_count = excluded.unread_count,
                muted = excluded.muted,
                pinned = excluded.pinned,
                pin_rank = excluded.pin_rank,
                archived = excluded.archived,
                last_message_id = excluded.last_message_id,
                last_date = excluded.last_date,
                last_preview = excluded.last_preview
            """,
            [
                (
                    c.account_id,
                    c.chat_id,
                    c.title,
                    c.kind.value,
                    c.unread_count,
                    1 if c.muted else 0,
                    1 if c.pinned else 0,
                    c.pin_rank,
                    1 if c.archived else 0,
                    c.last_message_id,
                    _format_iso(c.last_date),
                    c.last_preview,
                )
                for c in chats
            ],
        )


def delete_chats(conn: sqlite3.Connection, account_id: int, chat_ids: Sequence[int]) -> None:
    if not chat_ids:
        return
    placeholders = ",".join("?" for _ in chat_ids)
    with conn:
        conn.execute(
            f"DELETE FROM chats WHERE account_id = ? AND chat_id IN ({placeholders})",
            [account_id, *chat_ids],
        )


# Seconds the chat list waits before a delete is sent to Telegram.
# Matches the undo toast on the chat screen.
CHAT_DELETE_UNDO_SECONDS = 5.0


def schedule_chat_deletes(
    conn: sqlite3.Connection,
    account_id: int,
    chat_ids: Sequence[int],
    delay: float = CHAT_DELETE_UNDO_SECONDS,
) -> None:
    """Remember chats whose Telegram dialog should be deleted after `delay`."""
    if not chat_ids:
        return
    not_before = time.time() + delay
    with conn:
        conn.executemany(
            """
            INSERT INTO pending_chat_deletes (account_id, chat_id, not_before, attempts)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(account_id, chat_id) DO UPDATE SET
                not_before = excluded.not_before,
                attempts = 0
            """,
            [(account_id, int(cid), not_before) for cid in chat_ids],
        )


def cancel_chat_deletes(conn: sqlite3.Connection, account_id: int, chat_ids: Sequence[int]) -> None:
    """Drop pending Telegram deletes, for example after undo."""
    if not chat_ids:
        return
    placeholders = ",".join("?" for _ in chat_ids)
    with conn:
        conn.execute(
            f"DELETE FROM pending_chat_deletes WHERE account_id = ? AND chat_id IN ({placeholders})",
            [account_id, *chat_ids],
        )


def pending_delete_ids(conn: sqlite3.Connection, account_id: int) -> set[int]:
    rows = conn.execute(
        "SELECT chat_id FROM pending_chat_deletes WHERE account_id = ?",
        (account_id,),
    ).fetchall()
    return {int(r["chat_id"]) for r in rows}


def claim_due_chat_deletes(
    conn: sqlite3.Connection,
    limit: int = 10,
    now: float | None = None,
    force: bool = False,
) -> list[tuple[int, int, int]]:
    """Take deletes out of the queue. Returns (account_id, chat_id, attempts).

    Due rows are those whose undo window has closed. `force` takes every
    pending row, which is how shutdown sends a delete the user did not undo.
    """
    current = time.time() if now is None else now
    with conn:
        if force:
            rows = conn.execute(
                """
                SELECT account_id, chat_id, attempts
                FROM pending_chat_deletes
                ORDER BY not_before ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT account_id, chat_id, attempts
                FROM pending_chat_deletes
                WHERE not_before <= ?
                ORDER BY not_before ASC
                LIMIT ?
                """,
                (current, limit),
            ).fetchall()
        claimed = [(int(r["account_id"]), int(r["chat_id"]), int(r["attempts"])) for r in rows]
        for account_id, chat_id, _attempts in claimed:
            conn.execute(
                "DELETE FROM pending_chat_deletes WHERE account_id = ? AND chat_id = ?",
                (account_id, chat_id),
            )
    return claimed


def dialog_state_needs_push(conn: sqlite3.Connection, account_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM pushed_dialog_state WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    return row is None


def mark_dialog_state_pushed(conn: sqlite3.Connection, account_id: int) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO pushed_dialog_state (account_id) VALUES (?)",
            (account_id,),
        )


def saved_archive_and_mute_ids(conn: sqlite3.Connection, account_id: int) -> tuple[list[int], list[int]]:
    """Chat ids this install has archived or muted and not yet told Telegram about."""
    rows = conn.execute(
        """
        SELECT chat_id, archived, muted
        FROM chats
        WHERE account_id = ? AND (archived = 1 OR muted = 1)
        ORDER BY chat_id
        """,
        (account_id,),
    ).fetchall()
    archived = [int(r["chat_id"]) for r in rows if r["archived"]]
    muted = [int(r["chat_id"]) for r in rows if r["muted"]]
    return archived, muted


def reschedule_chat_delete(
    conn: sqlite3.Connection,
    account_id: int,
    chat_id: int,
    attempts: int,
    delay: float = CHAT_DELETE_UNDO_SECONDS,
) -> None:
    """Put a failed delete back, unless undo already restored that chat."""
    with conn:
        conn.execute(
            """
            INSERT INTO pending_chat_deletes (account_id, chat_id, not_before, attempts)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id, chat_id) DO UPDATE SET
                not_before = excluded.not_before,
                attempts = excluded.attempts
            """,
            (account_id, chat_id, time.time() + delay, attempts),
        )


def archive_chats(conn: sqlite3.Connection, account_id: int, chat_ids: Sequence[int], archived: bool = True) -> None:
    if not chat_ids:
        return
    placeholders = ",".join("?" for _ in chat_ids)
    with conn:
        conn.execute(
            f"UPDATE chats SET archived = ? WHERE account_id = ? AND chat_id IN ({placeholders})",
            [1 if archived else 0, account_id, *chat_ids],
        )


def mute_chats(conn: sqlite3.Connection, account_id: int, chat_ids: Sequence[int], muted: bool = True) -> None:
    if not chat_ids:
        return
    placeholders = ",".join("?" for _ in chat_ids)
    with conn:
        conn.execute(
            f"UPDATE chats SET muted = ? WHERE account_id = ? AND chat_id IN ({placeholders})",
            [1 if muted else 0, account_id, *chat_ids],
        )


def mark_chats_read(conn: sqlite3.Connection, account_id: int, chat_ids: Sequence[int], read: bool = True) -> None:
    if not chat_ids:
        return
    placeholders = ",".join("?" for _ in chat_ids)
    val = 0 if read else 1
    with conn:
        conn.execute(
            f"UPDATE chats SET unread_count = ? WHERE account_id = ? AND chat_id IN ({placeholders})",
            [val, account_id, *chat_ids],
        )


# Message operations
def get_messages(
    conn: sqlite3.Connection,
    account_id: int,
    chat_id: int,
    limit: int = 50,
    before_id: int | None = None,
    search_query: str = "",
) -> list[Message]:
    """Messages in chronological order, oldest first.

    Local placeholders (queued or failed sends) have negative ids. They are
    the newest messages, so they come last and never in an older page.
    """
    params: list[object] = [account_id, chat_id]
    # A delete still inside its undo window is hidden, not gone.
    where = """
        WHERE account_id = ? AND chat_id = ?
        AND NOT EXISTS (
            SELECT 1 FROM pending_message_deletes p
            WHERE p.account_id = m.account_id
            AND p.chat_id = m.chat_id
            AND p.message_id = m.message_id
        )
    """
    if search_query:
        where += " AND (plain_text LIKE ? OR sender_name LIKE ?)"
        params.append(f"%{search_query}%")
        params.append(f"%{search_query}%")

    sent_sql = f"SELECT * FROM messages m {where} AND message_id > 0"
    sent_params = list(params)
    if before_id is not None:
        sent_sql += " AND message_id < ?"
        sent_params.append(before_id)
    sent_sql += " ORDER BY message_id DESC LIMIT ?"
    sent_params.append(limit)
    sent = conn.execute(sent_sql, sent_params).fetchall()

    local = []
    if before_id is None:
        # -1 was made before -2, so descending ids is the order they were sent.
        local = conn.execute(
            f"SELECT * FROM messages m {where} AND message_id < 0 ORDER BY message_id DESC",
            params,
        ).fetchall()

    # Reverse to return in chronological order (oldest to newest)
    return [_message_from_row(r) for r in reversed(sent)] + [
        _message_from_row(r) for r in local
    ]


def _message_from_row(r: sqlite3.Row) -> Message:
    return Message(
        account_id=r["account_id"],
        chat_id=r["chat_id"],
        message_id=r["message_id"],
        sender_id=r["sender_id"],
        sender_name=r["sender_name"],
        date=_parse_iso(r["date"]),
        kind=MessageKind(r["kind"]),
        plain_text=r["plain_text"] or "",
        caption=r["caption"] or "",
        entity_spans=json.loads(r["entity_spans"]) if r["entity_spans"] else [],
        reply_id=r["reply_id"],
        forward_label=r["forward_label"] or "",
        edited=bool(r["edited"]),
        outgoing=bool(r["outgoing"]),
        send_state=SendState(r["send_state"]),
        is_deleted=bool(r["is_deleted"]),
        local_path=r["local_path"] or "",
    )


def get_message(
    conn: sqlite3.Connection,
    account_id: int,
    chat_id: int,
    message_id: int,
) -> Message | None:
    row = conn.execute(
        "SELECT * FROM messages WHERE account_id = ? AND chat_id = ? AND message_id = ?",
        (account_id, chat_id, message_id),
    ).fetchone()
    if not row:
        return None
    return _message_from_row(row)


def upsert_messages(conn: sqlite3.Connection, messages: Sequence[Message]) -> None:
    with conn:
        conn.executemany(
            """
            INSERT INTO messages (
                account_id, chat_id, message_id, sender_id, sender_name,
                date, kind, plain_text, caption, entity_spans,
                reply_id, forward_label, edited, outgoing, send_state, is_deleted,
                local_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, chat_id, message_id) DO UPDATE SET
                sender_id = excluded.sender_id,
                sender_name = excluded.sender_name,
                date = excluded.date,
                kind = excluded.kind,
                plain_text = excluded.plain_text,
                caption = excluded.caption,
                entity_spans = excluded.entity_spans,
                reply_id = excluded.reply_id,
                forward_label = excluded.forward_label,
                edited = excluded.edited,
                outgoing = excluded.outgoing,
                send_state = excluded.send_state,
                is_deleted = excluded.is_deleted,
                local_path = excluded.local_path
            """,
            [
                (
                    m.account_id,
                    m.chat_id,
                    m.message_id,
                    m.sender_id,
                    m.sender_name,
                    _format_iso(m.date),
                    m.kind.value,
                    m.plain_text,
                    m.caption,
                    json.dumps(m.entity_spans),
                    m.reply_id,
                    m.forward_label,
                    1 if m.edited else 0,
                    1 if m.outgoing else 0,
                    m.send_state.value,
                    1 if m.is_deleted else 0,
                    m.local_path,
                )
                for m in messages
            ],
        )


def delete_messages(
    conn: sqlite3.Connection,
    account_id: int,
    chat_id: int,
    message_ids: Sequence[int],
) -> None:
    if not message_ids:
        return
    placeholders = ",".join("?" for _ in message_ids)
    with conn:
        conn.execute(
            f"""
            UPDATE messages
            SET is_deleted = 1, plain_text = '[deleted]'
            WHERE account_id = ? AND chat_id = ? AND message_id IN ({placeholders})
            """,
            [account_id, chat_id, *message_ids],
        )


# Seconds a message delete waits before it is sent to Telegram.
# Matches the undo toast on the message screen.
MESSAGE_DELETE_UNDO_SECONDS = 5.0

# How long a claimed delete stays hidden while Telegram answers. A crash
# inside it leaves the row, so the next start sends the delete again.
MESSAGE_DELETE_LEASE_SECONDS = 60.0


def schedule_message_deletes(
    conn: sqlite3.Connection,
    account_id: int,
    chat_id: int,
    message_ids: Sequence[int],
    revoke: bool,
    delay: float = MESSAGE_DELETE_UNDO_SECONDS,
) -> None:
    """Hide messages now and delete them on Telegram after `delay`."""
    if not message_ids:
        return
    not_before = time.time() + delay
    with conn:
        conn.executemany(
            """
            INSERT INTO pending_message_deletes
                (account_id, chat_id, message_id, revoke, not_before, attempts)
            VALUES (?, ?, ?, ?, ?, 0)
            ON CONFLICT(account_id, chat_id, message_id) DO UPDATE SET
                revoke = excluded.revoke,
                not_before = excluded.not_before,
                attempts = 0
            """,
            [
                (account_id, chat_id, int(mid), 1 if revoke else 0, not_before)
                for mid in message_ids
            ],
        )


def cancel_message_deletes(
    conn: sqlite3.Connection,
    account_id: int,
    chat_id: int,
    message_ids: Sequence[int],
) -> None:
    """Drop pending deletes so the messages show again, for example after undo."""
    if not message_ids:
        return
    placeholders = ",".join("?" for _ in message_ids)
    with conn:
        conn.execute(
            f"""
            DELETE FROM pending_message_deletes
            WHERE account_id = ? AND chat_id = ? AND message_id IN ({placeholders})
            """,
            [account_id, chat_id, *message_ids],
        )


def claim_due_message_deletes(
    conn: sqlite3.Connection,
    limit: int = 100,
    now: float | None = None,
    force: bool = False,
) -> list[tuple[int, int, bool, int, list[int]]]:
    """Lease deletes whose undo window closed, grouped for one Telegram call.

    Returns (account_id, chat_id, revoke, attempts, message_ids). The rows
    stay, so the messages stay hidden until finish or cancel. `force` takes
    every row, which is how shutdown sends a delete the user did not undo.
    """
    current = time.time() if now is None else now
    with conn:
        if force:
            rows = conn.execute(
                """
                SELECT account_id, chat_id, message_id, revoke, attempts
                FROM pending_message_deletes
                ORDER BY not_before ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT account_id, chat_id, message_id, revoke, attempts
                FROM pending_message_deletes
                WHERE not_before <= ?
                ORDER BY not_before ASC
                LIMIT ?
                """,
                (current, limit),
            ).fetchall()
        groups: dict[tuple[int, int, bool], tuple[int, list[int]]] = {}
        for r in rows:
            key = (int(r["account_id"]), int(r["chat_id"]), bool(r["revoke"]))
            attempts, ids = groups.get(key, (0, []))
            ids.append(int(r["message_id"]))
            groups[key] = (max(attempts, int(r["attempts"] or 0)), ids)
            conn.execute(
                """
                UPDATE pending_message_deletes SET not_before = ?
                WHERE account_id = ? AND chat_id = ? AND message_id = ?
                """,
                (current + MESSAGE_DELETE_LEASE_SECONDS, *key[:2], int(r["message_id"])),
            )
    return [
        (account_id, chat_id, revoke, attempts, ids)
        for (account_id, chat_id, revoke), (attempts, ids) in groups.items()
    ]


def finish_message_deletes(
    conn: sqlite3.Connection,
    account_id: int,
    chat_id: int,
    message_ids: Sequence[int],
) -> None:
    """Telegram took the delete. Remove the rows and their queue entries."""
    if not message_ids:
        return
    placeholders = ",".join("?" for _ in message_ids)
    params = [account_id, chat_id, *message_ids]
    # The connection autocommits each statement. Removing the messages first
    # means no reader sees them unhidden but not yet deleted.
    with conn:
        conn.execute(
            f"""
            DELETE FROM messages
            WHERE account_id = ? AND chat_id = ? AND message_id IN ({placeholders})
            """,
            params,
        )
        conn.execute(
            f"""
            DELETE FROM pending_message_deletes
            WHERE account_id = ? AND chat_id = ? AND message_id IN ({placeholders})
            """,
            params,
        )


def reschedule_message_deletes(
    conn: sqlite3.Connection,
    account_id: int,
    chat_id: int,
    message_ids: Sequence[int],
    attempts: int,
    delay: float = MESSAGE_DELETE_UNDO_SECONDS,
) -> None:
    """Try a failed delete again later. Undo may already have removed the rows."""
    if not message_ids:
        return
    placeholders = ",".join("?" for _ in message_ids)
    with conn:
        conn.execute(
            f"""
            UPDATE pending_message_deletes SET not_before = ?, attempts = ?
            WHERE account_id = ? AND chat_id = ? AND message_id IN ({placeholders})
            """,
            [time.time() + delay, attempts, account_id, chat_id, *message_ids],
        )


def min_message_id(conn: sqlite3.Connection) -> int:
    """Lowest message id stored. Local placeholders count down below zero."""
    row = conn.execute("SELECT MIN(message_id) AS low FROM messages").fetchone()
    if row is None or row["low"] is None:
        return 0
    return int(row["low"])


# Draft operations
def get_draft(conn: sqlite3.Connection, account_id: int, chat_id: int) -> Draft | None:
    row = conn.execute(
        "SELECT * FROM drafts WHERE account_id = ? AND chat_id = ?",
        (account_id, chat_id),
    ).fetchone()
    if not row:
        return None
    return Draft(
        account_id=row["account_id"],
        chat_id=row["chat_id"],
        text=row["text"],
        reply_to_id=row["reply_to_id"],
        edit_message_id=row["edit_message_id"],
    )


def save_draft(conn: sqlite3.Connection, draft: Draft) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO drafts (account_id, chat_id, text, reply_to_id, edit_message_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(account_id, chat_id) DO UPDATE SET
                text = excluded.text,
                reply_to_id = excluded.reply_to_id,
                edit_message_id = excluded.edit_message_id
            """,
            (
                draft.account_id,
                draft.chat_id,
                draft.text,
                draft.reply_to_id,
                draft.edit_message_id,
            ),
        )


def clear_draft(conn: sqlite3.Connection, account_id: int, chat_id: int) -> None:
    with conn:
        conn.execute(
            "DELETE FROM drafts WHERE account_id = ? AND chat_id = ?",
            (account_id, chat_id),
        )


# Transcript operations
def get_transcript(
    conn: sqlite3.Connection,
    account_id: int,
    chat_id: int,
    message_id: int,
) -> Transcript | None:
    row = conn.execute(
        "SELECT * FROM transcripts WHERE account_id = ? AND chat_id = ? AND message_id = ?",
        (account_id, chat_id, message_id),
    ).fetchone()
    if not row:
        return None
    return Transcript(
        account_id=row["account_id"],
        chat_id=row["chat_id"],
        message_id=row["message_id"],
        text=row["text"] or "",
        model=row["model"] or "",
        error=row["error"] or "",
        created_at=_parse_iso(row["created_at"]),
    )


def upsert_transcript(conn: sqlite3.Connection, transcript: Transcript) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO transcripts (account_id, chat_id, message_id, text, model, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, chat_id, message_id) DO UPDATE SET
                text = excluded.text,
                model = excluded.model,
                error = excluded.error,
                created_at = excluded.created_at
            """,
            (
                transcript.account_id,
                transcript.chat_id,
                transcript.message_id,
                transcript.text,
                transcript.model,
                transcript.error,
                _format_iso(transcript.created_at),
            ),
        )
