"""Pure rendering functions for tg-cli domain models.
Formats chats and messages into Rich Text objects without requiring Textual widgets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence
from rich.cells import cell_len
from rich.style import Style
from rich.text import Text
from tg_cli.domain.models import Chat, ChatKind, Message, MessageKind, SendState


def truncate_to_width(text: str, max_width: int) -> str:
    """Truncate text so display cell width does not exceed max_width."""
    if cell_len(text) <= max_width:
        return text

    current_len = 0
    chars: list[str] = []
    for ch in text:
        ch_len = cell_len(ch)
        if current_len + ch_len > max_width - 1:  # leave room for ellipsis
            break
        chars.append(ch)
        current_len += ch_len
    return "".join(chars) + "…"


def format_time(dt: datetime | None) -> str:
    if dt is None:
        return ""
    # Ensure UTC or local format
    return dt.strftime("%H:%M")


def format_date_separator(dt: datetime | None) -> str:
    if dt is None:
        return "Unknown Date"
    return dt.strftime("%A, %B %d, %Y")


def get_kind_glyph(kind: ChatKind) -> str:
    match kind:
        case ChatKind.CHANNEL:
            return "📢"
        case ChatKind.GROUP:
            return "👥"
        case ChatKind.BOT:
            return "🤖"
        case _:
            return ""


def render_chat_row(
    chat: Chat,
    width: int,
    is_cursor: bool = False,
    is_selected: bool = False,
    is_archive_header: bool = False,
) -> Text:
    """Render a single chat row fitting exactly within width cells."""
    line = Text()

    # Cursor / selection prefix (4 cells: "> " or "  ", plus selection mark)
    cursor_mark = "> " if is_cursor else "  "
    select_mark = "[x] " if is_selected else "    "
    prefix = f"{cursor_mark}{select_mark if is_selected else ''}"
    line.append(prefix, style="bold cyan" if is_cursor else "default")

    if is_archive_header:
        line.append("[Archive]", style="bold blue")
        return line

    # Pin / Mute indicators
    status_icons = ""
    if chat.pinned:
        status_icons += "📌"
    if chat.muted:
        status_icons += "🔇"

    glyph = get_kind_glyph(chat.kind)

    # Time string & unread badge for the right side
    time_str = format_time(chat.last_date)
    unread_str = f" {chat.unread_count} " if chat.unread_count > 0 else ""

    # Calculate available space
    used_cells = cell_len(prefix)
    if status_icons:
        used_cells += cell_len(status_icons) + 1
    if glyph:
        used_cells += cell_len(glyph) + 1

    right_side_cells = cell_len(time_str) + (cell_len(unread_str) + 1 if unread_str else 0) + 1
    available_for_title_and_preview = max(10, width - used_cells - right_side_cells)

    # Title gets ~40% of middle, preview gets ~60%
    title_max = max(12, int(available_for_title_and_preview * 0.45))
    preview_max = max(10, available_for_title_and_preview - title_max - 2)

    title_clean = truncate_to_width(chat.title, title_max)
    preview_clean = truncate_to_width(chat.last_preview, preview_max)

    # Append icons
    if status_icons:
        line.append(status_icons + " ", style="dim")
    if glyph:
        line.append(glyph + " ", style="cyan")

    # Title styling: bold if unread, dim if muted
    title_style = "bold white" if chat.unread_count > 0 else "white"
    if chat.muted:
        title_style = "dim"
    line.append(title_clean, style=title_style)

    # Space padding between title and preview
    title_padding = " " * max(1, title_max - cell_len(title_clean) + 1)
    line.append(title_padding)

    # Preview
    if preview_clean.startswith("Draft: "):
        line.append("Draft: ", style="bold red")
        line.append(preview_clean[7:], style="red")
    else:
        preview_style = "dim italic" if chat.muted else "dim"
        line.append(preview_clean, style=preview_style)

    # Right-aligned time and unread count
    total_so_far = cell_len(line.plain)
    needed_pad = max(1, width - total_so_far - right_side_cells)
    line.append(" " * needed_pad)

    if time_str:
        line.append(time_str, style="dim")

    if unread_str:
        line.append(" ")
        line.append(
            unread_str,
            style="black on dark_cyan" if chat.muted else "bold white on blue",
        )

    if is_cursor:
        # Highlight cursor row subtly
        line.stylize("on grey15" if not is_selected else "on navy_blue")
    elif is_selected:
        line.stylize("on grey19")

    return line


def render_message(
    message: Message,
    width: int,
    expanded: bool = False,
    transcript: str | None = None,
    is_cursor: bool = False,
    is_selected: bool = False,
) -> list[Text]:
    """Render a message into 1 or more Text lines.
    Clamped to 4 lines unless expanded=True.
    """
    lines: list[Text] = []

    cursor_prefix = "> " if is_cursor else "  "
    select_mark = "[x] " if is_selected else ""
    header_prefix = f"{cursor_prefix}{select_mark}"

    # Header line: Sender Name + Timestamp (+ state)
    header = Text()
    header.append(header_prefix, style="bold cyan" if is_cursor else "default")

    sender_style = "bold green" if message.outgoing else "bold cyan"
    header.append(message.sender_name or "Unknown", style=sender_style)
    header.append("  ")

    time_str = format_time(message.date)
    header.append(time_str, style="dim")

    if message.edited:
        header.append(" (edited)", style="dim italic")

    if message.send_state == SendState.QUEUED:
        header.append(" [queued]", style="yellow")
    elif message.send_state == SendState.FAILED:
        header.append(" [failed - Enter to retry]", style="bold red")

    lines.append(header)

    # Quoted reply line if any
    if message.reply_id:
        reply_line = Text()
        reply_line.append(f"{' ' * len(header_prefix)}↳ reply to #{message.reply_id}", style="dim italic")
        lines.append(reply_line)

    # Forward label if any
    if message.forward_label:
        fwd_line = Text()
        fwd_line.append(f"{' ' * len(header_prefix)}fwd from {message.forward_label}", style="dim italic")
        lines.append(fwd_line)

    # Deleted message
    if message.is_deleted:
        del_line = Text()
        del_line.append(f"{' ' * len(header_prefix)}[deleted]", style="dim italic red")
        lines.append(del_line)
        return lines

    # Body lines based on MessageKind
    body_text = ""
    match message.kind:
        case MessageKind.TEXT:
            body_text = message.plain_text
        case MessageKind.PHOTO:
            body_text = f"[photo] {message.caption}".strip()
        case MessageKind.VIDEO:
            body_text = f"[video] {message.caption}".strip()
        case MessageKind.VOICE:
            body_text = message.plain_text or "[voice]"
            if transcript:
                body_text += f"\n  transcript: {transcript}"
        case MessageKind.FILE:
            body_text = f"[file] {message.plain_text}"
        case MessageKind.STICKER:
            body_text = "[sticker]"
        case MessageKind.GIF:
            body_text = f"[gif] {message.caption}".strip()
        case MessageKind.LOCATION:
            body_text = f"[location] {message.plain_text}"
        case MessageKind.CONTACT:
            body_text = f"[contact] {message.plain_text}"
        case MessageKind.POLL:
            body_text = f"[poll] {message.plain_text}"
        case _:
            body_text = "[message]"

    indent = " " * len(header_prefix)
    raw_lines = body_text.splitlines() or [""]

    # Wrap or clamp lines
    max_body_lines = 1000 if expanded else 4
    for idx, r_line in enumerate(raw_lines[:max_body_lines]):
        line_item = Text()
        line_item.append(indent)
        truncated = truncate_to_width(r_line, width - len(indent) - 2)
        line_item.append(truncated)
        lines.append(line_item)

    if not expanded and len(raw_lines) > max_body_lines:
        more_line = Text()
        more_line.append(f"{indent}... [press 'z' to expand {len(raw_lines) - max_body_lines} more lines]", style="dim italic yellow")
        lines.append(more_line)

    return lines
