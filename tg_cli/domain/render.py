"""Pure rendering functions for tg-cli domain models.
Formats chats and messages into Rich Text objects without requiring Textual widgets.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Sequence
import unicodedata
from rich.cells import cached_cell_len as _ORIGINAL_CACHED_CELL_LEN
from rich.cells import cell_len as _ORIGINAL_CELL_LEN
from rich.style import Style
from rich.text import Text
from tg_cli.domain.models import Chat, ChatKind, Message, MessageKind, SendState

try:
    import wcwidth
    _HAS_WCWIDTH = True
except ImportError:
    _HAS_WCWIDTH = False

# Unicode UTS #51 BMP codepoints that have Emoji_Presentation=Yes (default double-width 2 cells without VS16).
# All other BMP characters (e.g. ✓, ★, ©, ®, ™, ▶, ◀, •, ♥) default to text presentation (1 cell) unless followed by \ufe0f (VS16).
BMP_EMOJI_PRESENTATION_RANGES = (
    (0x231A, 0x231B),  # ⌚, ⌛
    (0x23E9, 0x23EC),  # ⏩, ⏪, ⏫, ⏬
    (0x23F0, 0x23F0),  # ⏰
    (0x23F3, 0x23F3),  # ⏳
    (0x25FD, 0x25FE),  # ◽, ◾
    (0x2614, 0x2615),  # ☔, ☕
    (0x2648, 0x2653),  # ♈..♓ Zodiac
    (0x267F, 0x267F),  # ♿
    (0x2693, 0x2693),  # ⚓
    (0x26A1, 0x26A1),  # ⚡
    (0x26AA, 0x26AB),  # ⚪, ⚫
    (0x26BD, 0x26BE),  # ⚽, ⚾
    (0x26C4, 0x26C5),  # ⛄, ⛅
    (0x26CE, 0x26CE),  # ⛎
    (0x26D4, 0x26D4),  # ⛔
    (0x26EA, 0x26EA),  # ⛪
    (0x26F2, 0x26F3),  # ⛲, ⛳
    (0x26F5, 0x26F5),  # ⛵
    (0x26FA, 0x26FA),  # ⛺
    (0x26FD, 0x26FD),  # ⛽
    (0x2705, 0x2705),  # ✅
    (0x270A, 0x270B),  # ✊, ✋
    (0x2728, 0x2728),  # ✨
    (0x274C, 0x274C),  # ❌
    (0x274E, 0x274E),  # ❎
    (0x2753, 0x2755),  # ❓, ❔, ❕
    (0x2757, 0x2757),  # ❗
    (0x2795, 0x2797),  # ➕, ➖, ➗
    (0x27B0, 0x27B0),  # ➰
    (0x27BF, 0x27BF),  # ➿
    (0x2B1B, 0x2B1C),  # ⬛, ⬜
    (0x2B50, 0x2B50),  # ⭐
    (0x2B55, 0x2B55),  # ⭕
)


def split_graphemes(text: str) -> list[str]:
    """Split text into unicode grapheme clusters, preserving ZWJ, flags, keycaps, and modifiers."""
    if not text:
        return []

    clusters: list[str] = []
    curr: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        curr.append(ch)
        i += 1
        # Regional indicator pairs (country flags like 🇺🇸, 🇺🇿)
        if 0x1F1E6 <= ord(ch) <= 0x1F1FF:
            if i < n and 0x1F1E6 <= ord(text[i]) <= 0x1F1FF:
                curr.append(text[i])
                i += 1
            clusters.append("".join(curr))
            curr = []
            continue
        # Absorb combining marks (including enclosing marks like keycaps), variation selectors, skin tone modifiers, and ZWJ sequences
        while i < n:
            next_ch = text[i]
            code = ord(next_ch)
            cat = unicodedata.category(next_ch)
            if (
                cat[0] == "M"
                or 0xFE00 <= code <= 0xFE0F
                or 0x1F3FB <= code <= 0x1F3FF
                or 0xE0100 <= code <= 0xE01EF
                or next_ch == "\u200d"
            ):
                curr.append(next_ch)
                i += 1
                if next_ch == "\u200d" and i < n:
                    curr.append(text[i])
                    i += 1
            else:
                break
        clusters.append("".join(curr))
        curr = []
    return clusters


def is_emoji_cluster(cluster: str) -> bool:
    """Return True if grapheme cluster renders as an emoji in modern terminals."""
    if not cluster:
        return False
    # If text presentation selector VS15 is present, it forces 1-cell monochrome presentation
    if "\ufe0e" in cluster:
        return False
    # Emoji presentation selector VS16
    if "\ufe0f" in cluster:
        return True
    # ZWJ sequence, keycap sequence, or skin tone modifier
    if "\u200d" in cluster or "\u20e3" in cluster:
        return True
    if any(0x1F3FB <= ord(c) <= 0x1F3FF for c in cluster):
        return True
    # Regional indicator pairs (country flags)
    if len(cluster) >= 2 and 0x1F1E6 <= ord(cluster[0]) <= 0x1F1FF:
        return True

    cp0 = ord(cluster[0])
    # SMP emoji & pictograph blocks (0x1F000 - 0x1FAFF)
    if 0x1F000 <= cp0 <= 0x1FAFF:
        return True

    # Exact Unicode UTS #51 BMP emoji presentation ranges
    for start, end in BMP_EMOJI_PRESENTATION_RANGES:
        if start <= cp0 <= end:
            return True

    return False


def get_emoji_cell_width(cluster: str = "") -> int:
    """Return cell width for one emoji grapheme on Windows Terminal (default 2)."""
    override = os.environ.get("TG_CLI_EMOJI_WIDTH")
    if override:
        try:
            return max(1, min(2, int(override.strip())))
        except ValueError:
            pass
    return 2


def _needs_emoji_measure(text: str) -> bool:
    """True when Rich's per-codepoint sum can disagree with Windows Terminal."""
    for ch in text:
        code = ord(ch)
        if (
            code == 0x200D  # ZWJ
            or code == 0xFE0F  # VS16
            or code == 0x20E3  # keycap
            or code >= 0x1F000  # SMP emoji / flags / skin tones
        ):
            return True
    return False


def _is_bmp_emoji_presentation(code: int) -> bool:
    for start, end in BMP_EMOJI_PRESENTATION_RANGES:
        if start <= code <= end:
            return True
        if code < start:
            return False
    return False


def _base_char_width(ch: str) -> int:
    """Width of one base character. Joiners and selectors are counted separately."""
    cp = ord(ch)
    # Enclosing keycap and combining marks fuse. Skin tones do not: they are
    # wide (EAW=W) and this console leaves them as their own cell.
    if cp == 0x20E3 or unicodedata.category(ch).startswith("M"):
        return 0
    # Regional indicators are EAW=N but are drawn as two wide letters.
    if 0x1F1E6 <= cp <= 0x1F1FF:
        return get_emoji_cell_width()
    # Wide and fullwidth characters, including skin tones and emoji
    # pictographs. Neutral SMP symbols (🏛 U+1F3DB, 🖼 U+1F5BC) stay narrow
    # unless a variation selector adds a cell.
    if _is_bmp_emoji_presentation(cp) or unicodedata.east_asian_width(ch) in ("W", "F"):
        return get_emoji_cell_width()
    return _ORIGINAL_CELL_LEN(ch)


def cluster_terminal_width(cluster: str) -> int:
    """Cell width of one grapheme as this console advances the cursor.

    A wide pictograph is 2 cells (👤 📊 🚀, and the chat-type emblems).
    Measured rows that still drifted:

    - 🙏🏻 is U+1F64F plus a skin tone. The tone is not fused, so the
      cluster is 4. Three of them sit in the Tekhron M preview.
    - 🏛 and 🖼 are neutral-width symbols with no variation selector.
      Forcing every SMP character to 2 pushed Andijon Startaplari and
      the idbot preview.
    - 🎤︎ is U+1F3A4 plus VS15. The selector still advances a cell, same
      as VS16, so the Shazam title is 3.
    - Flags stay two wide letters (4). VS16 on an already-wide mark is
      +1 (❗ is 3). ZWJ is +1 and does not fuse.
    """
    if not cluster:
        return 0
    if _ORIGINAL_CELL_LEN(cluster) <= 0 and not any(unicodedata.east_asian_width(ch) in ("W", "F") for ch in cluster):
        return 0
    total = 0
    for ch in cluster:
        code = ord(ch)
        # VS16, VS15, and ZWJ are not zero-width on this console.
        if code in (0xFE0F, 0xFE0E, 0x200D):
            total += 1
            continue
        total += _base_char_width(ch)
    return total


def terminal_cell_width(text: str) -> int:
    """Display width of text using Windows Terminal grapheme widths."""
    if not text:
        return 0
    if text.isascii() or not _needs_emoji_measure(text):
        return _ORIGINAL_CELL_LEN(text)
    return sum(cluster_terminal_width(cluster) for cluster in split_graphemes(text))


def grapheme_cell_width(cluster: str) -> int:
    """Measure the display cell width of a single grapheme cluster."""
    return cluster_terminal_width(cluster)


def cell_width(text: str) -> int:
    """Calculate display cell width matching Windows Terminal and Textual."""
    return terminal_cell_width(text)


def slice_by_cells(text: str, start: int, end: int | None) -> str:
    """Return the slice of `text` between two display columns.

    `start` is inclusive and `end` is exclusive. `end is None` runs to the
    end of the line. Wide characters are kept whole.
    """
    if not text:
        return ""
    if start < 0:
        start = 0
    if end is not None and end <= start:
        return ""
    if start == 0 and end is None:
        return text
    out: list[str] = []
    col = 0
    for cluster in split_graphemes(text):
        width = cell_width(cluster)
        if end is not None and col >= end:
            break
        if col >= start:
            out.append(cluster)
        col += width
    return "".join(out)


def apply_terminal_cell_width() -> None:
    """Point Rich and Textual at terminal grapheme widths.

    Textual crops each chat row with Rich's cell_len. If that sum disagrees
    with the cursor, the crop either clips the clock or the terminal draws
    the emoji past the column we padded for. Replacing the imported names
    keeps the crop, the padding, and the cursor on the same scale.
    """
    import sys

    import rich.cells as cells

    originals = (_ORIGINAL_CELL_LEN, _ORIGINAL_CACHED_CELL_LEN)
    cells.cell_len = terminal_cell_width
    cells.cached_cell_len = terminal_cell_width
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        for name in ("cell_len", "cached_cell_len"):
            current = getattr(mod, name, None)
            if current in originals:
                try:
                    setattr(mod, name, terminal_cell_width)
                except (AttributeError, TypeError):
                    pass


def to_local_datetime(dt: datetime | None) -> datetime | None:
    """Convert UTC or naive datetime to the user's system clock/local timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def truncate_to_width(text: str, max_width: int, ellipsis: str = "…") -> str:
    """Truncate text so display cell width does not exceed max_width."""
    if max_width <= 0:
        return ""
    if cell_width(text) <= max_width:
        return text

    ell_width = cell_width(ellipsis)
    if max_width <= ell_width:
        res = ellipsis
        clusters = split_graphemes(res)
        while clusters and cell_width("".join(clusters)) > max_width:
            clusters.pop()
        return "".join(clusters)

    target_width = max_width - ell_width
    current_width = 0
    res_clusters: list[str] = []
    for cluster in split_graphemes(text):
        cw = cell_width(cluster)
        if current_width + cw > target_width:
            break
        res_clusters.append(cluster)
        current_width += cw

    res = "".join(res_clusters) + ellipsis
    clusters = split_graphemes(res)
    while clusters and cell_width("".join(clusters)) > max_width:
        clusters.pop()
    return "".join(clusters)


# Blank letters. They are not Unicode spaces, but this console draws them as a
# gap. U+3164 is wide, so a leading one pushes the preview two cells right.
_PREVIEW_BLANK_LETTERS = str.maketrans({
    "\u115f": " ",  # HANGUL CHOSEONG FILLER
    "\u1160": " ",  # HANGUL JUNGSEONG FILLER
    "\u2800": " ",  # BRAILLE PATTERN BLANK
    "\u3164": " ",  # HANGUL FILLER
    "\uffa0": " ",  # HALFWIDTH HANGUL FILLER
})


def format_chat_preview(text: str, max_width: int) -> str:
    """Collapse message preview into a singular line and truncate with triple dots."""
    if not text or max_width <= 0:
        return ""

    collapsed = " ".join(text.translate(_PREVIEW_BLANK_LETTERS).split())
    if not collapsed:
        return ""

    is_multiline = "\n" in text or "\r" in text
    if cell_width(collapsed) > max_width:
        res = truncate_to_width(collapsed, max_width, ellipsis="...")
    elif is_multiline and not collapsed.endswith("..."):
        if cell_width(collapsed) + 3 <= max_width:
            res = collapsed + "..."
        else:
            res = truncate_to_width(collapsed, max_width, ellipsis="...")
    else:
        res = collapsed

    while cell_width(res) > max_width:
        clusters = split_graphemes(res)
        if not clusters:
            break
        clusters.pop()
        res = "".join(clusters)
    return res


def format_time(dt: datetime | None) -> str:
    """Format time as HH:MM adjusted to the user's system clock."""
    if dt is None:
        return ""
    local_dt = to_local_datetime(dt)
    return local_dt.strftime("%H:%M") if local_dt else ""


def format_chat_time(dt: datetime | None, *, now: datetime | None = None) -> str:
    """Chat-list stamp from the local calendar, not a rolling 24-hour window.

    Same local date as now → HH:MM. Same local year, any other day → dd/mm.
    Any other year → that year, for example 2022. Message clocks stay on format_time.
    """
    local_dt = to_local_datetime(dt)
    if local_dt is None:
        return ""
    local_now = to_local_datetime(now if now is not None else datetime.now(timezone.utc))
    if local_now is None:
        return ""
    if local_dt.date() == local_now.date():
        return local_dt.strftime("%H:%M")
    if local_dt.year == local_now.year:
        return local_dt.strftime("%d/%m")
    return str(local_dt.year)


def format_date_separator(dt: datetime | None) -> str:
    """Format date separator adjusted to the user's system clock."""
    if dt is None:
        return "Unknown Date"
    local_dt = to_local_datetime(dt)
    return local_dt.strftime("%A, %B %d, %Y") if local_dt else "Unknown Date"


def get_kind_glyph(kind: ChatKind) -> str:
    match kind:
        case ChatKind.USER:
            return "👤"
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
    has_selection: bool = False,
) -> Text:
    """Render a single chat row fitting strictly within width cells."""
    line = Text()

    # Cursor / selection prefix
    cursor_mark = "> " if is_cursor else "  "
    if has_selection or is_selected:
        prefix = f"{cursor_mark}{'[x] ' if is_selected else '[ ] '}"
    else:
        prefix = cursor_mark

    if is_archive_header:
        line.append(prefix, style="bold cyan" if is_cursor else "default")
        line.append("[Archive]", style="bold blue")
        return line

    glyph = get_kind_glyph(chat.kind)
    glyph_part = f"{glyph} " if glyph else "   "
    # Kind emblems stay the original emoji. A single emblem is one 2-cell
    # grapheme plus the following space, so this column remains 3 cells.
    glyph_cells = cell_width(glyph_part) if glyph_part else 0

    # Determine column lengths based on window width
    if width < 50:
        unread_width = 4
    elif width < 120:
        unread_width = 5
    else:
        unread_width = 6

    time_width = 5
    prefix_cells = cell_width(prefix)

    # Right-side fixed block: space(1) + time_width(5) + space(1) + unread_width
    right_side_cells = 1 + time_width + 1 + unread_width

    # Guaranteed whitespace gap before time column so preview never invades the right side
    buffer_gap = 3 if width >= 60 else 1
    left_fixed = prefix_cells + glyph_cells

    # Total middle space available between left columns and right columns
    total_middle = max(8, width - left_fixed - 1 - right_side_cells)

    title_ratio = 0.38 if width < 80 else (0.35 if width < 120 else 0.30)
    title_width = max(6, int(total_middle * title_ratio))

    # Preview stops safely before the right columns, leaving buffer_gap for spaces
    preview_max_width = max(6, total_middle - title_width - buffer_gap)

    # 1. Title Column (strictly title_width cells)
    status_icons = ""
    if chat.pinned:
        status_icons += "📌"
    if chat.muted:
        status_icons += "🔇"

    status_part = f" {status_icons}" if status_icons else ""
    status_len = cell_width(status_part)

    avail_title = max(1, title_width - status_len)
    title_raw = " ".join(chat.title.split()) if chat.title else ""
    title_clean = truncate_to_width(title_raw, avail_title, ellipsis="...")

    # Strict clamping: ensure title_clean + status_part never exceeds title_width
    while cell_width(title_clean) + status_len > title_width:
        clusters = split_graphemes(title_clean)
        if clusters:
            clusters.pop()
            title_clean = "".join(clusters)
        else:
            status_part = ""
            status_len = 0
            break

    title_col = Text()
    title_style = "bold white" if chat.unread_count > 0 else "white"
    if chat.muted:
        title_style = "dim"
    title_col.append(title_clean, style=title_style)
    if status_part:
        title_col.append(status_part, style="dim")

    # Double-check that title_col never exceeds title_width
    while cell_width(title_col.plain) > title_width:
        clusters = split_graphemes(title_col.plain)
        if not clusters:
            break
        clusters.pop()
        title_col = Text("".join(clusters), style=title_style)

    used_in_title = cell_width(title_col.plain)
    if used_in_title < title_width:
        title_col.append(" " * (title_width - used_in_title))

    # 2. Message Preview Column (capped to preview_max_width, ending safely to the left)
    preview_raw = chat.last_preview or ""
    preview_clean = format_chat_preview(preview_raw, preview_max_width)
    while cell_width(preview_clean) > preview_max_width:
        clusters = split_graphemes(preview_clean)
        if not clusters:
            break
        clusters.pop()
        preview_clean = "".join(clusters)

    preview_col = Text()
    if preview_clean.startswith("Draft: "):
        preview_col.append("Draft: ", style="bold red")
        preview_col.append(preview_clean[7:], style="red")
    else:
        preview_style = "dim italic" if chat.muted else "dim"
        preview_col.append(preview_clean, style=preview_style)

    # Double-check that preview_col never exceeds preview_max_width
    while cell_width(preview_col.plain) > preview_max_width:
        clusters = split_graphemes(preview_col.plain)
        if not clusters:
            break
        clusters.pop()
        preview_col = Text("".join(clusters), style=preview_style)

    # Assemble line up to preview
    line.append(prefix, style="bold cyan" if is_cursor else "default")
    line.append(glyph_part, style="cyan")
    line.append_text(title_col)
    line.append(" ")
    line.append_text(preview_col)

    # 3. Dynamic space padding to push time and unread to the exact right boundary
    used_so_far = cell_width(line.plain)
    pad_needed = max(1, width - used_so_far - (time_width + 1 + unread_width))
    line.append(" " * pad_needed)

    # 4. Stamp column (strictly time_width cells): HH:MM, dd/mm, or the year.
    time_str = format_chat_time(chat.last_date) or ""
    time_clean = time_str[:time_width]
    pad_time = max(0, time_width - cell_width(time_clean))
    line.append((" " * pad_time) + time_clean, style="dim")
    line.append(" ")

    # 5. Dedicated Unread Count Column (strictly unread_width cells).
    # The whole column is one rectangle, so "1" and "99+" are the same size.
    # Counts above 99 clamp to 99+.
    unread_col = Text()
    if chat.unread_count > 0:
        label = "99+" if chat.unread_count > 99 else str(chat.unread_count)
        if cell_width(label) > unread_width:
            label = label[:unread_width]
        pad = unread_width - cell_width(label)
        left = pad // 2
        badge = (" " * left) + label + (" " * (pad - left))
        unread_col.append(
            badge,
            style="black on dark_cyan" if chat.muted else "bold white on blue",
        )
    else:
        unread_col.append(" " * unread_width)

    line.append_text(unread_col)

    if is_cursor:
        # Highlight cursor row subtly
        line.stylize("on grey15" if not is_selected else "on navy_blue")
    elif is_selected:
        line.stylize("on grey19")

    return line


def get_message_summary(message: Message) -> str:
    """Return plain-text representation of message content for quotes and previews."""
    match message.kind:
        case MessageKind.TEXT:
            return message.plain_text
        case MessageKind.PHOTO:
            return f"[photo] {message.caption}".strip() if message.caption else "[photo]"
        case MessageKind.VIDEO:
            return f"[video] {message.caption}".strip() if message.caption else "[video]"
        case MessageKind.VOICE:
            return message.plain_text or "[voice]"
        case MessageKind.FILE:
            return f"[file] {message.plain_text}".strip() if message.plain_text else "[file]"
        case MessageKind.STICKER:
            return "[sticker]"
        case MessageKind.GIF:
            return f"[gif] {message.caption}".strip() if message.caption else "[gif]"
        case MessageKind.LOCATION:
            return f"[location] {message.plain_text}".strip() if message.plain_text else "[location]"
        case MessageKind.CONTACT:
            return f"[contact] {message.plain_text}".strip() if message.plain_text else "[contact]"
        case MessageKind.POLL:
            return f"[poll] {message.plain_text}".strip() if message.plain_text else "[poll]"
        case MessageKind.SERVICE:
            return message.plain_text or "service message"
        case _:
            return message.plain_text or "[message]"


# Muted colors for a dark background. Outgoing is warm sand. Incoming in a
# direct chat is steel blue. Group names cycle the rest, none of them neon.
_OUTGOING_COLOR = "#c4b48a"
_INCOMING_COLOR = "#8eabc4"
_GROUP_NAME_COLORS = (
    "#8eabc4",
    "#a3b18a",
    "#d4b483",
    "#c48b8b",
    "#b0a0c4",
    "#7eaea4",
    "#c4a3b0",
    "#9aab84",
)
COLLAPSED_BODY_LINES = 12
# Message text wraps here. The count is display columns, so a wide emoji
# takes its real width and a word that would pass this limit moves intact.
MESSAGE_LINE_MAX = 70
# Group message text sits under the name, not beside it.
GROUP_BODY_PAD = "    "


def author_color(message: Message, *, direct: bool) -> str:
    """Color for one author. Direct chats use two fixed colors."""
    if message.outgoing:
        return _OUTGOING_COLOR
    if direct:
        return _INCOMING_COLOR
    key = message.sender_id or sum(ord(ch) for ch in (message.sender_name or "?"))
    return _GROUP_NAME_COLORS[key % len(_GROUP_NAME_COLORS)]


def same_author(a: Message, b: Message) -> bool:
    """True when two messages belong to one person.

    A service line is not part of a person's run, so the next real message
    prints its name again.
    """
    if a.kind == MessageKind.SERVICE or b.kind == MessageKind.SERVICE:
        return False
    if a.outgoing and b.outgoing:
        return True
    if a.sender_id and b.sender_id:
        return a.sender_id == b.sender_id and a.outgoing == b.outgoing
    return a.sender_name == b.sender_name and a.outgoing == b.outgoing


# A burst stays one bundle while every message is within this long of the
# first one. One minute covers the same clock minute, including a message
# that lands just on the other side of it.
BUNDLE_WINDOW = timedelta(seconds=60)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def same_bundle(head: Message, message: Message) -> bool:
    """True when message continues head's bundle.

    The window is measured from the first message, so a slow drip of
    messages a minute apart starts a new clock instead of hiding every time.
    """
    if not same_author(head, message):
        return False
    if head.date is None or message.date is None:
        return head.date is None and message.date is None
    return abs(_as_utc(message.date) - _as_utc(head.date)) <= BUNDLE_WINDOW


def message_bundle_flags(items: Sequence[object]) -> tuple[dict[int, bool], dict[int, bool]]:
    """Return (show_time, gap_after) for each message.

    show_time is true on the first message of a bundle. gap_after is false
    when the next stream item continues that bundle. A date line or the
    unread divider ends the bundle, so the message under it shows a time.
    """
    show_time: dict[int, bool] = {}
    gap_after: dict[int, bool] = {}
    head: Message | None = None
    prev: Message | None = None
    for item in items:
        if not isinstance(item, Message):
            head = None
            if prev is not None:
                gap_after[prev.message_id] = True
                prev = None
            continue
        continues = head is not None and same_bundle(head, item)
        show_time[item.message_id] = not continues
        if not continues:
            head = item
        if prev is not None:
            gap_after[prev.message_id] = not continues
        prev = item
    if prev is not None:
        gap_after[prev.message_id] = True
    return show_time, gap_after


def sender_header_flags(items: Sequence[object], *, direct: bool) -> dict[int, bool]:
    """Whether each message should print its sender name.

    Direct chats never print a name. Group chats print it on the first
    message of a run. Date lines and the unread divider do not break a run,
    so the closest name above still owns the messages under it.
    """
    show: dict[int, bool] = {}
    prev: Message | None = None
    for item in items:
        if not isinstance(item, Message):
            continue
        if direct:
            show[item.message_id] = False
        else:
            show[item.message_id] = prev is None or not same_author(prev, item)
        prev = item
    return show


def collapse_newlines(text: str) -> str:
    """Keep one line break between content. Drop blank lines."""
    kept = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(kept)


def wrap_words(text: str, limit: int) -> list[str]:
    """Wrap text so each line is at most limit columns, breaking before a word.

    A word wider than limit is split by grapheme so the line still fits.
    """
    limit = max(1, limit)
    if text == "":
        return [""]
    clusters = split_graphemes(text)
    widths = [cell_width(cluster) for cluster in clusters]
    if sum(widths) <= limit:
        return [text]

    lines: list[str] = []
    i = 0
    n = len(clusters)
    while i < n:
        width = 0
        j = i
        last_space: int | None = None
        while j < n and width + widths[j] <= limit:
            width += widths[j]
            if clusters[j].isspace():
                last_space = j
            j += 1
        if j == n:
            tail = "".join(clusters[i:]).rstrip()
            if tail:
                lines.append(tail)
            break
        if j == i:
            lines.append(clusters[i])
            i += 1
            continue
        if last_space is None or last_space < i:
            lines.append("".join(clusters[i:j]))
            i = j
            continue
        piece = "".join(clusters[i:last_space]).rstrip()
        if piece:
            lines.append(piece)
        i = last_space + 1
        while i < n and clusters[i].isspace():
            i += 1
    return lines or [""]


def _wrap_block(text: str, limit: int) -> list[str]:
    """Wrap each source line. Existing line breaks stay where the author put them."""
    wrapped: list[str] = []
    for source in text.splitlines() or [""]:
        wrapped.extend(wrap_words(source, limit))
    return wrapped or [""]


def _render_service_message(
    message: Message,
    width: int,
    *,
    header_prefix: str,
    prefix_style: str,
    clock: str,
    role: str,
) -> list[Text]:
    """One dim line: the actor, an optional role, and what happened."""
    body = message.plain_text or "service message"
    name = (message.sender_name or "").strip()
    if role and name and name != "Unknown" and body.startswith(name):
        body = f"{name} {role}{body[len(name):]}"
    clock_part = f"{clock}  " if clock else ""
    indent = cell_width(header_prefix) + cell_width(clock_part)
    limit = min(MESSAGE_LINE_MAX, max(1, width - indent - 1))
    lines: list[Text] = []
    for index, part in enumerate(wrap_words(body, limit)):
        line = Text()
        if index == 0:
            line.append(header_prefix, style=prefix_style)
            if clock_part:
                line.append(clock_part, style="dim")
        else:
            line.append(" " * indent)
        line.append(part, style="dim italic")
        lines.append(line)
    return lines or [Text("")]


def render_message(
    message: Message,
    width: int,
    expanded: bool = False,
    transcript: str | None = None,
    is_cursor: bool = False,
    is_selected: bool = False,
    replied_message: Message | None = None,
    show_sender: bool = True,
    direct: bool = False,
    show_time: bool = True,
    role: str = "",
) -> list[Text]:
    """Render a message into one or more Text lines.

    Direct chats omit the name and color the text by author. Group chats
    print the name once per run and indent the body under it. A bundle
    prints the clock on its first message only. Collapsed messages show
    COLLAPSED_BODY_LINES lines, then [press 'z' to expand].
    `role` is "admin" or "owner" and is printed after the name.
    """
    lines: list[Text] = []

    cursor_prefix = "> " if is_cursor else "  "
    select_mark = "[x] " if is_selected else ""
    header_prefix = f"{cursor_prefix}{select_mark}"
    prefix_style = "bold cyan" if is_cursor else "default"
    prefix_indent = " " * cell_width(header_prefix)
    body_indent = prefix_indent if direct else prefix_indent + GROUP_BODY_PAD
    color = author_color(message, direct=direct)
    clock = format_time(message.date)
    if message.kind == MessageKind.SERVICE:
        return _render_service_message(
            message,
            width,
            header_prefix=header_prefix,
            prefix_style=prefix_style,
            clock=clock if show_time else "",
            role=role if role in ("admin", "owner") else "",
        )
    show_name = not direct and show_sender
    has_status = (
        message.edited
        or message.send_state == SendState.QUEUED
        or message.send_state == SendState.FAILED
    )
    header_visible = show_name or (show_time and bool(clock)) or has_status

    header = Text()
    header.append(header_prefix, style=prefix_style)
    if show_name:
        header.append(message.sender_name or "Unknown", style=color)
        if role in ("admin", "owner"):
            header.append(f" {role}", style="dim")
        header.append("  ")
    if show_time and clock:
        header.append(clock, style="dim")
    if message.edited:
        header.append(" (edited)", style="dim italic")
    if message.send_state == SendState.QUEUED:
        header.append(" [queued]", style="yellow")
    elif message.send_state == SendState.FAILED:
        header.append(" [failed - Enter to retry]", style="bold red")

    body_text = collapse_newlines(get_message_summary(message))
    if message.kind == MessageKind.VOICE and transcript:
        extra = collapse_newlines(transcript)
        if extra:
            body_text = f"{body_text}\ntranscript: {extra}" if body_text else f"transcript: {extra}"

    # Direct chats put the first line of a new bundle on the time row.
    # Later messages in the bundle line up under that text, and the cursor
    # mark stays in the left margin.
    normal_col = cell_width(f"  {clock}  ") if clock else cell_width("    ")
    direct_pad = max(0, normal_col - cell_width(header_prefix))
    body_col = cell_width(body_indent)
    will_attach = (
        header_visible
        and direct
        and show_time
        and not message.reply_id
        and not message.forward_label
        and not message.is_deleted
        and bool(body_text.strip())
    )
    if will_attach:
        text_col = cell_width(header.plain) + cell_width("  ")
    elif not header_visible:
        extra = direct_pad if direct else cell_width(GROUP_BODY_PAD)
        text_col = cell_width(header_prefix) + extra
    else:
        text_col = cell_width(body_indent)
    line_limit = min(MESSAGE_LINE_MAX, max(1, width - text_col - 1))
    raw_lines = _wrap_block(body_text, line_limit)
    max_body_lines = 1000 if expanded else COLLAPSED_BODY_LINES
    shown = raw_lines[:max_body_lines]
    attach_first = (
        header_visible
        and direct
        and show_time
        and not message.reply_id
        and not message.forward_label
        and not message.is_deleted
        and bool(shown)
        and bool(shown[0])
    )
    if attach_first:
        header.append("  ")
        body_col = cell_width(header.plain)
        avail = max(1, width - body_col - 1)
        header.append(truncate_to_width(shown[0], avail), style=color)
    if header_visible:
        lines.append(header)

    started_content = header_visible
    wrap_indent = " " * body_col if (direct and attach_first) else body_indent

    def begin_line() -> Text:
        """Indent one content line. The first line of a continuation keeps the cursor."""
        nonlocal started_content
        line = Text()
        if not started_content:
            line.append(header_prefix, style=prefix_style)
            line.append(" " * direct_pad if direct else GROUP_BODY_PAD)
            started_content = True
        elif not header_visible:
            line.append(" " * cell_width(header_prefix))
            line.append(" " * direct_pad if direct else GROUP_BODY_PAD)
        else:
            line.append(wrap_indent)
        return line

    # Quoted reply, indented with the body so it belongs to this message.
    if message.reply_id:
        if replied_message is not None:
            sender_name = replied_message.sender_name
            if not sender_name:
                sender_name = "You" if replied_message.outgoing else "Unknown"
            reply_body = collapse_newlines(get_message_summary(replied_message))
            quote_chrome = cell_width(body_indent) + cell_width("> ") + cell_width(f"{sender_name}: ")
            quote_limit = min(MESSAGE_LINE_MAX, max(1, width - quote_chrome - 1))
            reply_lines = _wrap_block(reply_body, quote_limit)
            max_reply_lines = 1000 if expanded else 2
            for r_idx, r_text in enumerate(reply_lines[:max_reply_lines]):
                q_line = begin_line()
                q_line.append("> ", style="dim")
                if r_idx == 0:
                    q_line.append(f"{sender_name}: ", style="dim")
                    used = cell_width(q_line.plain)
                    avail_w = max(4, width - used - 1)
                else:
                    q_line.append("  ")
                    used = cell_width(q_line.plain)
                    avail_w = max(4, width - used - 1)
                q_line.append(truncate_to_width(r_text, avail_w), style="dim italic")
                lines.append(q_line)
            if not expanded and len(reply_lines) > max_reply_lines:
                q_more = begin_line()
                q_more.append("> ...", style="dim italic")
                lines.append(q_more)
        else:
            reply_line = begin_line()
            reply_line.append(f"> reply to #{message.reply_id}", style="dim italic")
            lines.append(reply_line)

    if message.forward_label:
        fwd_line = begin_line()
        fwd_line.append(f"fwd from {message.forward_label}", style="dim italic")
        lines.append(fwd_line)

    if message.is_deleted:
        del_line = begin_line()
        del_line.append("[deleted]", style="dim italic red")
        lines.append(del_line)
        return lines

    body_lines = shown[1:] if attach_first else shown
    text_style = color if direct else "default"
    for r_line in body_lines:
        line_item = begin_line()
        avail = max(1, width - cell_width(line_item.plain) - 1)
        line_item.append(truncate_to_width(r_line, avail), style=text_style)
        lines.append(line_item)

    if not expanded and len(raw_lines) > max_body_lines:
        more_line = begin_line()
        more_line.append("[press 'z' to expand]", style="dim italic")
        lines.append(more_line)

    return lines


apply_terminal_cell_width()
