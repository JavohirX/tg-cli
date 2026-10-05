"""Single source of truth for all keyboard shortcuts and commands in tg-cli.
The footer, the help screen, and the command palette all read this table.
No shortcut string gets copied into a second place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True, slots=True)
class Command:
    id: str
    title: str
    keys: tuple[str, ...]
    display_key: str
    contexts: tuple[str, ...]  # e.g. "global", "list", "accounts", "chats", "messages", "composer"
    undoable: bool = False
    destructive: bool = False
    description: str = ""


COMMANDS: tuple[Command, ...] = (
    # Global
    Command(
        id="palette",
        title="Command Palette",
        keys=("ctrl+k",),
        display_key="Ctrl+K",
        contexts=("global", "accounts", "chats", "messages", "composer"),
        description="Open command palette to search and run actions",
    ),
    Command(
        id="help",
        title="Help",
        keys=("?", "f1"),
        display_key="?",
        contexts=("global", "accounts", "chats", "messages"),
        description="Show keyboard shortcut reference",
    ),
    Command(
        id="account_next",
        title="Next Account",
        keys=("f2",),
        display_key="F2",
        contexts=("global", "chats", "messages"),
        description="Switch to next authenticated account",
    ),
    Command(
        id="account_prev",
        title="Previous Account",
        keys=("shift+f2",),
        display_key="Shift+F2",
        contexts=("global", "chats", "messages"),
        description="Switch to previous authenticated account",
    ),
    Command(
        id="focus_toggle",
        title="Toggle List/Write",
        keys=("tab",),
        display_key="Tab",
        contexts=("messages", "chats"),
        description="Switch focus between list navigation and composer",
    ),
    Command(
        id="back",
        title="Back / Cancel",
        keys=("escape",),
        display_key="Esc",
        contexts=("global", "accounts", "chats", "messages", "composer"),
        description="Go back, clear selection, close popup, or defocus",
    ),
    Command(
        id="cancel",
        title="Cancel In-Flight",
        keys=("ctrl+c",),
        display_key="Ctrl+C",
        contexts=("global",),
        description="Cancel pending action; double Ctrl+C to quit",
    ),

    # List Navigation
    Command(
        id="move_down",
        title="Move Down",
        keys=("j", "down"),
        display_key="j / ↓",
        contexts=("list", "accounts", "chats", "messages"),
        description="Move cursor down by 1 item",
    ),
    Command(
        id="move_up",
        title="Move Up",
        keys=("k", "up"),
        display_key="k / ↑",
        contexts=("list", "accounts", "chats", "messages"),
        description="Move cursor up by 1 item",
    ),
    Command(
        id="page_down_half",
        title="Half Page Down",
        keys=("ctrl+d",),
        display_key="Ctrl+D",
        contexts=("list", "chats", "messages"),
        description="Scroll half page down",
    ),
    Command(
        id="page_up_half",
        title="Half Page Up",
        keys=("ctrl+u",),
        display_key="Ctrl+U",
        contexts=("list", "chats", "messages"),
        description="Scroll half page up",
    ),
    Command(
        id="page_down",
        title="Page Down",
        keys=("pagedown",),
        display_key="PgDn",
        contexts=("list", "chats", "messages"),
        description="Scroll full page down",
    ),
    Command(
        id="page_up",
        title="Page Up",
        keys=("pageup",),
        display_key="PgUp",
        contexts=("list", "chats", "messages"),
        description="Scroll full page up",
    ),
    Command(
        id="jump_top",
        title="Jump to Top",
        keys=("home",),
        display_key="Home",
        contexts=("list", "chats", "messages"),
        description="Jump to top of list (first real chat)",
    ),
    Command(
        id="jump_bottom",
        title="Jump to Bottom",
        keys=("end",),
        display_key="End",
        contexts=("list", "chats", "messages"),
        description="Jump to bottom of list",
    ),

    # Selection
    Command(
        id="range_select",
        title="Range Select",
        keys=("v",),
        display_key="v",
        contexts=("list", "chats", "messages"),
        description="Set anchor for range selection; move cursor to extend",
    ),
    Command(
        id="toggle_select",
        title="Toggle Select",
        keys=("space",),
        display_key="Space",
        contexts=("list", "chats", "messages"),
        description="Toggle selection of focused item",
    ),
    Command(
        id="select_all",
        title="Select All Filtered",
        keys=("V",),
        display_key="V",
        contexts=("list", "chats"),
        description="Select all items in current filter (asks y/n if > 50)",
    ),

    # Folder Switching
    Command(
        id="folder_prev",
        title="Previous Folder",
        keys=("[",),
        display_key="[",
        contexts=("chats",),
        description="Switch to previous folder tab",
    ),
    Command(
        id="folder_next",
        title="Next Folder",
        keys=("]",),
        display_key="]",
        contexts=("chats",),
        description="Switch to next folder tab",
    ),

    # Chat Actions
    Command(
        id="open_chat",
        title="Open Chat",
        keys=("enter",),
        display_key="Enter",
        contexts=("chats",),
        description="Open messages for selected chat",
    ),
    Command(
        id="archive_chat",
        title="Archive / Unarchive",
        keys=("a",),
        display_key="a",
        contexts=("chats",),
        undoable=True,
        description="Toggle archive status for selected chat(s)",
    ),
    Command(
        id="mute_chat",
        title="Mute / Unmute",
        keys=("m",),
        display_key="m",
        contexts=("chats",),
        undoable=True,
        description="Toggle mute status for selected chat(s)",
    ),
    Command(
        id="mark_read",
        title="Mark as Read",
        keys=("r",),
        display_key="r",
        contexts=("chats",),
        undoable=True,
        description="Mark selected chat(s) as read",
    ),
    Command(
        id="mark_unread",
        title="Mark as Unread",
        keys=("R",),
        display_key="R",
        contexts=("chats",),
        undoable=True,
        description="Mark selected chat(s) as unread",
    ),
    Command(
        id="delete_chat",
        title="Delete Chat",
        keys=("d",),
        display_key="d",
        contexts=("chats",),
        undoable=True,
        destructive=True,
        description="Delete chat for me (undoable within 5s)",
    ),
    Command(
        id="undo",
        title="Undo Last Action",
        keys=("u",),
        display_key="u",
        contexts=("chats", "messages"),
        description="Undo last action within 5 seconds",
    ),
    Command(
        id="filter",
        title="Filter / Search",
        keys=("/",),
        display_key="/",
        contexts=("chats", "messages"),
        description="Filter chats or messages by text",
    ),

    # Message Actions
    Command(
        id="reply_message",
        title="Reply",
        keys=("r",),
        display_key="r",
        contexts=("messages",),
        description="Reply to focused message",
    ),
    Command(
        id="edit_message",
        title="Edit",
        keys=("e",),
        display_key="e",
        contexts=("messages",),
        description="Edit focused message (if sent by me)",
    ),
    Command(
        id="delete_message",
        title="Delete Message",
        keys=("d",),
        display_key="d",
        contexts=("messages",),
        undoable=True,
        destructive=True,
        description="Delete focused message",
    ),
    Command(
        id="transcribe_voice",
        title="Transcribe Voice",
        keys=("t",),
        display_key="t",
        contexts=("messages",),
        description="Transcribe voice message with Gemini",
    ),
    Command(
        id="retranscribe_voice",
        title="Force Re-transcribe",
        keys=("T",),
        display_key="T",
        contexts=("messages",),
        description="Force re-transcribe voice message ignoring cache",
    ),
    Command(
        id="yank_text",
        title="Copy Text",
        keys=("y",),
        display_key="y",
        contexts=("messages",),
        description="Copy message text to system clipboard",
    ),
    Command(
        id="open_link",
        title="Open Link",
        keys=("o",),
        display_key="o",
        contexts=("messages",),
        description="Open link in focused message in default browser",
    ),
    Command(
        id="toggle_expand",
        title="Expand / Spoiler",
        keys=("z",),
        display_key="z",
        contexts=("messages",),
        description="Expand clamped message text or reveal spoilers",
    ),

    # Composer Actions
    Command(
        id="send_message",
        title="Send",
        keys=("enter",),
        display_key="Enter",
        contexts=("composer",),
        description="Send typed message",
    ),
    Command(
        id="composer_newline",
        title="Insert Newline",
        keys=("ctrl+j", "shift+enter"),
        display_key="Ctrl+J",
        contexts=("composer",),
        description="Insert newline into composer (or \\ then Enter)",
    ),

    # Account Screen Actions
    Command(
        id="open_account",
        title="Open Account",
        keys=("enter",),
        display_key="Enter",
        contexts=("accounts",),
        description="Open selected account's chat list",
    ),
    Command(
        id="add_account",
        title="Add Account",
        keys=("a", "A"),
        display_key="A",
        contexts=("accounts",),
        description="Authenticate and add a new Telegram account",
    ),
    Command(
        id="remove_account",
        title="Remove Account",
        keys=("d", "D"),
        display_key="D",
        contexts=("accounts",),
        destructive=True,
        description="Remove selected account session and data",
    ),
)


_COMMAND_BY_ID: dict[str, Command] = {cmd.id: cmd for cmd in COMMANDS}


def get_command_by_id(cmd_id: str) -> Command | None:
    return _COMMAND_BY_ID.get(cmd_id)


def get_commands_for_context(context: str) -> list[Command]:
    """Return all commands applicable to a specific context or 'global'."""
    return [
        cmd
        for cmd in COMMANDS
        if context in cmd.contexts or "global" in cmd.contexts
    ]


def get_footer_hints(screen_context: str, is_write_mode: bool = False) -> list[tuple[str, str]]:
    """Return concise key-hint pairs for the footer based on current state."""
    if is_write_mode:
        return [
            ("Enter", "Send"),
            ("Ctrl+J", "Newline"),
            ("Tab", "List mode"),
            ("Esc", "Defocus"),
            ("Ctrl+K", "Palette"),
        ]

    if screen_context == "accounts":
        return [
            ("Enter", "Open"),
            ("A", "Add"),
            ("D", "Remove"),
            ("Ctrl+K", "Palette"),
            ("?", "Help"),
            ("Esc", "Exit"),
        ]

    if screen_context == "chats":
        return [
            ("Enter", "Open"),
            ("j/k", "Navigate"),
            ("v", "Select"),
            ("/", "Filter"),
            ("[ ]", "Folders"),
            ("F2", "Accounts"),
            ("Ctrl+K", "Palette"),
            ("?", "Help"),
        ]

    if screen_context == "messages":
        return [
            ("j/k", "Navigate"),
            ("Tab", "Write"),
            ("r", "Reply"),
            ("e", "Edit"),
            ("t", "Transcribe"),
            ("y", "Copy"),
            ("Ctrl+K", "Palette"),
            ("Esc", "Back"),
        ]

    return [
        ("Ctrl+K", "Palette"),
        ("?", "Help"),
        ("Esc", "Back"),
    ]
