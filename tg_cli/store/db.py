"""SQLite database schema and connection management.
Uses WAL mode for concurrent reads on UI thread and writes on Telethon thread.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from tg_cli.paths import get_db_path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    user_id INTEGER PRIMARY KEY,
    phone TEXT NOT NULL,
    username TEXT DEFAULT '',
    display_name TEXT DEFAULT '',
    session_path TEXT DEFAULT '',
    auth_state TEXT DEFAULT 'ok'
);

CREATE TABLE IF NOT EXISTS folders (
    account_id INTEGER NOT NULL,
    folder_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    position INTEGER DEFAULT 0,
    PRIMARY KEY (account_id, folder_id),
    FOREIGN KEY (account_id) REFERENCES accounts (user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chats (
    account_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    kind TEXT DEFAULT 'user',
    unread_count INTEGER DEFAULT 0,
    muted INTEGER DEFAULT 0,
    pinned INTEGER DEFAULT 0,
    pin_rank INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    last_message_id INTEGER DEFAULT 0,
    last_date TEXT,
    last_preview TEXT DEFAULT '',
    PRIMARY KEY (account_id, chat_id),
    FOREIGN KEY (account_id) REFERENCES accounts (user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_folders (
    account_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    folder_id INTEGER NOT NULL,
    PRIMARY KEY (account_id, chat_id, folder_id),
    FOREIGN KEY (account_id, chat_id) REFERENCES chats (account_id, chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    account_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    sender_id INTEGER DEFAULT 0,
    sender_name TEXT DEFAULT '',
    date TEXT,
    kind TEXT DEFAULT 'text',
    plain_text TEXT DEFAULT '',
    caption TEXT DEFAULT '',
    entity_spans TEXT DEFAULT '[]',
    reply_id INTEGER,
    forward_label TEXT DEFAULT '',
    edited INTEGER DEFAULT 0,
    outgoing INTEGER DEFAULT 0,
    send_state TEXT DEFAULT 'sent',
    is_deleted INTEGER DEFAULT 0,
    PRIMARY KEY (account_id, chat_id, message_id),
    FOREIGN KEY (account_id, chat_id) REFERENCES chats (account_id, chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS transcripts (
    account_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    text TEXT DEFAULT '',
    model TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TEXT,
    PRIMARY KEY (account_id, chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS drafts (
    account_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    reply_to_id INTEGER,
    edit_message_id INTEGER,
    PRIMARY KEY (account_id, chat_id),
    FOREIGN KEY (account_id, chat_id) REFERENCES chats (account_id, chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS history_window (
    account_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    oldest_loaded_id INTEGER NOT NULL,
    newest_loaded_id INTEGER NOT NULL,
    PRIMARY KEY (account_id, chat_id),
    FOREIGN KEY (account_id, chat_id) REFERENCES chats (account_id, chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chats_sort ON chats (account_id, archived, pinned DESC, pin_rank ASC, last_date DESC);
CREATE INDEX IF NOT EXISTS idx_messages_chat_date ON messages (account_id, chat_id, message_id DESC);
"""


def init_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Initialize SQLite database, applying schema and WAL mode."""
    target = Path(db_path) if db_path is not None else get_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0, autocommit=True)
    conn.row_factory = sqlite3.Row
    # PRAGMAs must run outside a transaction block
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        conn.executescript(SCHEMA_SQL)
    return conn
