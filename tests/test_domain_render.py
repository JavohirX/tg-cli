"""Tests for pure domain rendering and truncation."""

from datetime import datetime, timedelta, timezone
from rich.style import Style
from rich.text import Text
from tg_cli.domain.models import Chat, ChatKind, DateSeparator, Message, MessageKind, SendState, UnreadDivider
from tg_cli.domain.render import (
    cell_width,
    collapse_newlines,
    format_chat_preview,
    format_chat_time,
    format_time,
    get_kind_glyph,
    slice_by_cells,
    message_bundle_flags,
    render_chat_row,
    render_message,
    sender_header_flags,
    to_local_datetime,
    truncate_to_width,
    wrap_words,
)


def test_truncate_to_width_plain():
    text = "Hello World"
    assert truncate_to_width(text, 20) == "Hello World"
    assert truncate_to_width(text, 5) == "Hell…"


def test_truncate_to_width_cyrillic_and_emoji():
    # Emoji counts as 2 columns, Cyrillic typically 1 column each
    text = "Привет 🚀 мир!"
    truncated = truncate_to_width(text, 10)
    assert len(truncated) > 0
    # Must end with ellipsis
    assert truncated.endswith("…")


def test_render_chat_row():
    chat = Chat(
        account_id=1,
        chat_id=10,
        title="Bob 🚀",
        kind=ChatKind.USER,
        unread_count=3,
        pinned=True,
        muted=False,
        last_date=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
        last_preview="Hey there!",
    )
    rendered = render_chat_row(chat, width=80, is_cursor=True, is_selected=False)
    plain = rendered.plain
    assert "> " in plain
    assert "👤" in plain
    assert "Bob 🚀" in plain
    expected_time = format_chat_time(chat.last_date)
    assert expected_time in plain
    assert "3" in plain


def test_render_archive_row():
    chat = Chat(
        account_id=1,
        chat_id=-1000,
        title="[Archive]",
    )
    rendered = render_chat_row(chat, width=80, is_cursor=False, is_archive_header=True)
    assert "[Archive]" in rendered.plain


def test_render_message_clamping():
    long_text = "\n".join(f"Line {i}" for i in range(1, 15))
    msg = Message(
        account_id=1,
        chat_id=10,
        message_id=1,
        sender_name="Alice",
        plain_text=long_text,
    )
    # Collapsed messages keep 12 body lines, then the expand hint.
    lines_clamped = render_message(msg, width=80, expanded=False)
    clamped = "\n".join(line.plain for line in lines_clamped)
    assert "[press 'z' to expand]" in clamped
    assert "more lines" not in clamped
    assert "Line 12" in clamped
    assert "Line 13" not in clamped

    lines_expanded = render_message(msg, width=80, expanded=True)
    expanded = "\n".join(line.plain for line in lines_expanded)
    assert "[press 'z' to expand]" not in expanded
    assert "Line 14" in expanded


def test_render_message_kinds():
    voice_msg = Message(
        account_id=1,
        chat_id=10,
        message_id=2,
        sender_name="Bob",
        kind=MessageKind.VOICE,
        plain_text="[voice 0:45]",
    )
    lines = render_message(voice_msg, width=80, transcript="Arriving soon.")
    combined = "\n".join(l.plain for l in lines)
    assert "[voice 0:45]" in combined
    assert "transcript: Arriving soon." in combined


def test_format_chat_preview():
    # Multiline message collapsed into single line and ends with triple dots
    multiline = "Hello!\nHere is the update:\n1. Fixed bugs\n2. Done"
    res = format_chat_preview(multiline, 40)
    assert "\n" not in res
    assert "\r" not in res
    assert res.endswith("...")
    assert "Hello! Here is" in res

    # Long single-line message truncated to reasonable amount with triple dots
    long_msg = "This is a very long message that should easily exceed thirty characters of width"
    res2 = format_chat_preview(long_msg, 30)
    assert len(res2) <= 30
    assert res2.endswith("...")

    # Short single-line message
    short_msg = "Hey!"
    assert format_chat_preview(short_msg, 30) == "Hey!"

    # Empty message
    assert format_chat_preview("", 30) == ""

    # U+3164 is a wide blank letter. Spam puts it first, which indents the preview.
    assert format_chat_preview("\u3164You requested something", 40) == "You requested something"
    assert format_chat_preview("hello\u3164there", 40) == "hello there"
    assert format_chat_preview("\u115f\u1160\u2800\uffa0Hi", 40) == "Hi"


def test_render_chat_row_collapses_multiline():
    multiline_chat = Chat(
        account_id=1,
        chat_id=11,
        title="Engineering",
        kind=ChatKind.GROUP,
        unread_count=1,
        last_date=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
        last_preview="Sprint review notes:\n- Shipped feature A\n- In review B",
    )
    rendered = render_chat_row(multiline_chat, width=80)
    # The plain text must NOT contain any newlines (strictly a singular line)
    assert "\n" not in rendered.plain
    assert "\r" not in rendered.plain
    assert "Sprint review notes:" in rendered.plain
    assert "..." in rendered.plain


def test_hangul_filler_does_not_shift_the_preview_column():
    when = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    plain_chat = Chat(
        account_id=1,
        chat_id=1,
        title="Chat 8487910731",
        kind=ChatKind.USER,
        last_date=when,
        last_preview="You requested something long",
    )
    filler_chat = Chat(
        account_id=1,
        chat_id=2,
        title="Chat 8487910731",
        kind=ChatKind.USER,
        last_date=when,
        last_preview="\u3164You requested something long",
    )
    plain_row = render_chat_row(plain_chat, width=80)
    filler_row = render_chat_row(filler_chat, width=80)
    needle = "You requested"
    assert "\u3164" not in filler_row.plain
    assert plain_row.plain.find(needle) == filler_row.plain.find(needle)


def test_get_kind_glyph():
    assert get_kind_glyph(ChatKind.USER) == "👤"
    assert get_kind_glyph(ChatKind.CHANNEL) == "📢"
    assert get_kind_glyph(ChatKind.GROUP) == "👥"
    assert get_kind_glyph(ChatKind.BOT) == "🤖"


def test_render_chat_row_column_alignment():
    from rich.cells import cell_len

    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    chat_with_unread = Chat(
        account_id=1,
        chat_id=1,
        title="Bob 🚀",
        kind=ChatKind.USER,
        unread_count=5,
        pinned=True,
        last_date=now,
        last_preview="Hey there!",
    )
    chat_no_unread = Chat(
        account_id=1,
        chat_id=2,
        title="Engineering",
        kind=ChatKind.GROUP,
        unread_count=0,
        pinned=False,
        last_date=now,
        last_preview="Meeting notes attached",
    )

    row1 = render_chat_row(chat_with_unread, width=80)
    row2 = render_chat_row(chat_no_unread, width=80)

    plain1 = row1.plain
    plain2 = row2.plain

    assert cell_width(plain1) == 80
    assert cell_width(plain2) == 80

    # 1. Left side title alignment: title starts at cell 5 in both rows
    assert cell_width(plain1[:plain1.find("Bob 🚀")]) == 5
    assert cell_width(plain2[:plain2.find("Engineering")]) == 5

    # 2. Preview starts at the exact same display cell in both rows
    cell_prev1 = cell_width(plain1[:plain1.find("Hey there!")])
    cell_prev2 = cell_width(plain2[:plain2.find("Meeting notes")])
    assert cell_prev1 == cell_prev2 == 27

    # 3. Hours column starts at the exact same display cell in both rows
    # Unread counter does NOT move the timing line
    time_str = format_chat_time(now)
    cell_time1 = cell_width(plain1[:plain1.find(time_str)])
    cell_time2 = cell_width(plain2[:plain2.find(time_str)])
    assert cell_time1 == cell_time2 == 69

    # 4. Dedicated unread count column: row1 has badge, row2 has empty spaces
    idx1_time = plain1.find(time_str)
    idx2_time = plain2.find(time_str)
    assert "5" in plain1[idx1_time + len(time_str):]
    assert plain2[idx2_time + len(time_str):].strip() == ""


def test_unread_badge_is_fixed_size_and_clamps_at_99():
    """1 and 99+ share one rectangle; counts above 99 render as 99+."""
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

    def badge_span(count: int, width: int = 80) -> str:
        chat = Chat(
            account_id=1,
            chat_id=1,
            title="Ada",
            kind=ChatKind.USER,
            unread_count=count,
            last_date=now,
            last_preview="hello",
        )
        row = render_chat_row(chat, width=width)
        colored = []
        for span in row.spans:
            style = str(span.style)
            if "on blue" in style:
                colored.append(row.plain[span.start:span.end])
        assert len(colored) == 1
        return colored[0]

    one = badge_span(1)
    many = badge_span(42)
    capped = badge_span(100)
    capped_more = badge_span(250)
    assert cell_width(one) == cell_width(many) == cell_width(capped) == cell_width(capped_more)
    assert one.strip() == "1"
    assert many.strip() == "42"
    assert capped.strip() == "99+"
    assert capped_more.strip() == "99+"
    assert "100" not in capped
    assert "999+" not in capped_more
    # Same rectangle on a wide row, where the column itself is wider.
    assert cell_width(badge_span(1, width=140)) == cell_width(badge_span(150, width=140))


def test_render_chat_row_dedicated_lengths_differ_by_width():
    from rich.cells import cell_len

    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    chat = Chat(
        account_id=1,
        chat_id=1,
        title="Very Long Title That Would Be Truncated Differently In Different Widths",
        kind=ChatKind.CHANNEL,
        unread_count=12,
        last_date=now,
        last_preview="A very long message preview that definitely expands when given more window width",
    )

    r_narrow = render_chat_row(chat, width=50)
    r_med = render_chat_row(chat, width=80)
    r_wide = render_chat_row(chat, width=120)

    assert cell_len(r_narrow.plain) == 50
    assert cell_len(r_med.plain) == 80
    assert cell_len(r_wide.plain) == 120

    # In wider windows, the preview has more dedicated length and shows more text
    time_str = format_chat_time(now)
    assert cell_len(r_wide.plain.split(time_str)[0]) > cell_len(r_med.plain.split(time_str)[0])
    assert cell_len(r_med.plain.split(time_str)[0]) > cell_len(r_narrow.plain.split(time_str)[0])


def test_format_time_local_timezone():
    """Verify that timestamps are converted to local system clock rather than raw UTC."""
    # Construct a UTC datetime
    utc_dt = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    expected_local = utc_dt.astimezone().strftime("%H:%M")
    formatted = format_time(utc_dt)
    assert formatted == expected_local

    # Naive datetimes (assumed UTC from Telegram API)
    naive_dt = datetime(2026, 10, 5, 12, 0)
    expected_naive_local = naive_dt.replace(tzinfo=timezone.utc).astimezone().strftime("%H:%M")
    assert format_time(naive_dt) == expected_naive_local


def test_format_chat_time_follows_the_local_calendar():
    """Today is HH:MM, another day this year is dd/mm, another year is the year.

    Yesterday stays a date even when it is only a few hours ago.
    """
    local = datetime.now().astimezone().tzinfo
    now = datetime(2026, 10, 8, 1, 0, tzinfo=local)
    assert format_chat_time(datetime(2026, 10, 8, 0, 5, tzinfo=local), now=now) == "00:05"
    assert format_chat_time(datetime(2026, 10, 7, 23, 30, tzinfo=local), now=now) == "07/10"
    assert format_chat_time(datetime(2026, 3, 5, 15, 0, tzinfo=local), now=now) == "05/03"
    assert format_chat_time(datetime(2022, 12, 31, 23, 59, tzinfo=local), now=now) == "2022"
    assert format_chat_time(datetime(2031, 1, 2, 8, 0, tzinfo=local), now=now) == "2031"
    assert format_chat_time(None, now=now) == ""

    naive = datetime(2022, 6, 1, 12, 0)
    aware = datetime(2022, 6, 1, 12, 0, tzinfo=timezone.utc)
    assert format_chat_time(naive, now=now) == format_chat_time(aware, now=now) == "2022"

    current = datetime.now(timezone.utc)
    assert format_chat_time(current) == format_time(current)


def test_emoji_graphemes_use_terminal_cells_not_rich_sum():
    """Console cursor advance for the sequences that shoved four chat rows.

    A single pictograph is 2 cells, and so are the chat-type emblems.
    Flags are not ligated, VS16 still takes a cell, and ZWJ still takes a cell.
    """
    from tg_cli.domain.render import _ORIGINAL_CELL_LEN

    assert _ORIGINAL_CELL_LEN("❤️") == 1
    assert cell_width("❤️") == 2
    assert cell_width("\u263a\ufe0f") == 2  # ☺️ narrow symbol + VS16
    assert cell_width("\u2049\ufe0f") == 2  # ⁉️
    assert _ORIGINAL_CELL_LEN("\u2757\ufe0f") == 2
    assert cell_width("\u2757\ufe0f") == 3  # ❗️ already-wide mark + VS16
    assert cell_width("\u2757\ufe0f\u2757\ufe0f") == 6
    assert _ORIGINAL_CELL_LEN("🇺🇿") == 2
    assert cell_width("🇺🇿") == 4
    assert cell_width("🇪🇸") == 4
    assert _ORIGINAL_CELL_LEN("🧑\u200d⚕") == 3
    assert cell_width("🧑\u200d⚕") == 4  # person + ZWJ + staff
    assert _ORIGINAL_CELL_LEN("👨‍🎓") == 4
    assert cell_width("👨‍🎓") == 5  # man + ZWJ + cap
    assert cell_width("👨‍👩‍👧‍👦") == 11  # four people + three joiners
    assert _ORIGINAL_CELL_LEN("1️⃣") == 1
    assert cell_width("1️⃣") == 2
    assert _ORIGINAL_CELL_LEN("🖥") == 1
    assert cell_width("🖥") == 1  # neutral SMP symbol, no variation selector
    assert cell_width("🏛") == 1
    assert cell_width("🖼") == 1
    assert cell_width("🙏🏻") == 4  # folded hands + skin tone, not fused
    assert cell_width("\U0001f3a4\ufe0e") == 3  # 🎤︎ microphone + VS15
    assert cell_width("⭐\ufe0f") == 3
    assert cell_width("👤") == _ORIGINAL_CELL_LEN("👤") == 2
    assert cell_width("📢") == 2
    assert cell_width("👥") == 2
    assert cell_width("🤖") == 2
    assert cell_width("🙏") == 2
    assert cell_width("📊") == 2
    assert cell_width("💳") == 2
    assert cell_width("📍") == 2
    assert cell_width("😎") == 2
    assert cell_width("❤") == 1
    assert cell_width("⚠") == 1

    from rich.cells import cell_len

    assert cell_len("❤️") == 2
    assert cell_len("👨‍🎓") == 5
    assert cell_len("🇺🇿") == 4


def test_render_chat_row_emojis_do_not_shift_columns():
    """Verify that various emojis in titles and previews do not shift columns on terminals."""
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    test_cases = [
        ("Normal Chat", "Normal preview text"),
        ("Mom ❤️", "Love you! ☀️ See you"),
        ("Mom ❤", "Love you! See you"),  # Heart without VS16
        ("Dev Team 👨‍💻", "Fixed production bug ⚡️"),
        ("Family 👨‍👩‍👧‍👦 Group", "Birthday party photos 🎂"),
        ("USA 🇺🇸 Channel", "Breaking news ⚠️ alert!"),
        ("Tasks ✔", "Project finished ✈"),  # Checkmark & Airplane without VS16
        ("News ✓", "Update released"),  # Checkmark ✓
        ("Crypto ★", "Check this out ☆"),  # Stars ★ ☆
        ("Company ™", "Quarterly report ©"),  # Trademark & Copyright
        ("Media ▶", "Live broadcast ◀"),  # Triangles
        ("Bullet • Point", "Listing items ·"),  # Bullets
        ("Tech 🖥 News", "Desktop update released 🏎"),  # SMP emojis without VS16
        ("Tutorial 1️⃣", "Step one completed 🕶"),  # Keycap & sunglasses
        ("Alert ⚠ Notice", "Warning sign without VS16"),
    ]

    for title, preview in test_cases:
        c = Chat(
            account_id=1,
            chat_id=10,
            title=title,
            kind=ChatKind.USER,
            unread_count=5,
            pinned=True,
            last_date=now,
            last_preview=preview,
        )
        row = render_chat_row(c, width=80)
        # The entire row must fit within 80 terminal cells
        assert cell_width(row.plain) == 80, title
        # Preview and time stay on the same cells when the name or the
        # message contains emoji. Kind emblem column is still 👤 plus a space.
        assert row.plain.startswith("  👤 ")
        prev_idx = row.plain.find(preview)
        assert prev_idx >= 0, preview
        assert cell_width(row.plain[:prev_idx]) == 27, title
        time_str = format_chat_time(now)
        time_idx = row.plain.find(time_str)
        assert cell_width(row.plain[:time_idx]) == 69, title


def test_render_chat_row_selection_alignment():
    """Verify that rows remain aligned when selection mode is active."""
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    c1 = Chat(account_id=1, chat_id=1, title="Chat One", kind=ChatKind.USER, last_date=now)
    c2 = Chat(account_id=1, chat_id=2, title="Chat Two", kind=ChatKind.USER, last_date=now)

    row1 = render_chat_row(c1, width=80, is_selected=True, has_selection=True)
    row2 = render_chat_row(c2, width=80, is_selected=False, has_selection=True)

    assert cell_width(row1.plain) == 80
    assert cell_width(row2.plain) == 80

    time_str = format_chat_time(now)
    time_idx1 = row1.plain.find(time_str)
    time_idx2 = row2.plain.find(time_str)
    assert cell_width(row1.plain[:time_idx1]) == cell_width(row2.plain[:time_idx2])


def test_render_message_reply_quote():
    """Verify that replied messages are duplicated, indented, and prefixed with '>'."""
    original_msg = Message(
        account_id=1,
        chat_id=10,
        message_id=42,
        sender_name="Alice",
        kind=MessageKind.TEXT,
        plain_text="Are you free tonight?",
    )

    reply_msg = Message(
        account_id=1,
        chat_id=10,
        message_id=43,
        sender_name="Bob",
        kind=MessageKind.TEXT,
        plain_text="Yes, 8pm works!",
        reply_id=42,
        outgoing=True,
    )

    # Render with replied message available
    lines = render_message(reply_msg, width=80, replied_message=original_msg)
    full_text = "\n".join(l.plain for l in lines)

    # Quote and body sit under the name: two cursor spaces plus four pad spaces.
    assert "> Alice: Are you free tonight?" in full_text
    body_lines = [line.plain for line in lines if "Yes, 8pm works!" in line.plain]
    assert body_lines == ["      Yes, 8pm works!"]

    # Render without replied message available (fallback)
    lines_fallback = render_message(reply_msg, width=80, replied_message=None)
    fallback_text = "\n".join(l.plain for l in lines_fallback)
    assert "> reply to #42" in fallback_text


def _color_at(text: Text, needle: str) -> str | None:
    start = text.plain.index(needle)
    found: str | None = None
    for span in text.spans:
        if span.start <= start < span.end:
            style = span.style if isinstance(span.style, Style) else Style.parse(str(span.style))
            if style.color is not None:
                found = style.color.name.lower()
    return found


def test_wrap_words_moves_the_overflowing_word():
    assert wrap_words("a" * 70, 70) == ["a" * 70]
    assert wrap_words(("a" * 66) + " banana", 70) == ["a" * 66, "banana"]
    assert wrap_words("a" * 71, 70) == ["a" * 70, "a"]

    words = " ".join(["alpha"] * 40)
    wrapped = wrap_words(words, 70)
    assert " ".join(wrapped) == words
    assert all(cell_width(line) <= 70 for line in wrapped)
    assert all(part == "alpha" for line in wrapped for part in line.split())


def test_message_lines_wrap_at_70_in_every_chat():
    words = " ".join(["alpha"] * 40)
    when = datetime(2026, 10, 5, 12, 4, tzinfo=timezone.utc)
    for direct in (True, False):
        msg = Message(
            account_id=1,
            chat_id=10,
            message_id=1,
            sender_id=7,
            sender_name="Ada",
            date=when,
            plain_text=words,
            outgoing=True,
        )
        rendered = render_message(
            msg,
            width=200,
            direct=direct,
            show_sender=not direct,
            show_time=True,
        )
        pieces = []
        for line in rendered:
            if "alpha" not in line.plain:
                continue
            text = line.plain[line.plain.index("alpha"):]
            assert cell_width(text) <= 70
            assert "…" not in text
            pieces.append(text)
        assert " ".join(pieces) == words


def test_collapse_newlines_drops_blank_lines():
    assert collapse_newlines("hello \n \n \nthis is the 4th line") == "hello\nthis is the 4th line"
    assert collapse_newlines("hello\n\n\n\nthis is the 4th line") == "hello\nthis is the 4th line"
    assert collapse_newlines("  keep\n\n  indent") == "  keep\n  indent"
    assert collapse_newlines("\n \n") == ""


def test_direct_message_hides_name_and_colors_by_author():
    outgoing = Message(
        account_id=1,
        chat_id=10,
        message_id=1,
        sender_name="Me",
        plain_text="hello\n\n\nthere",
        outgoing=True,
    )
    incoming = Message(
        account_id=1,
        chat_id=10,
        message_id=2,
        sender_name="Ada",
        plain_text="hi back",
        outgoing=False,
    )
    out_lines = render_message(outgoing, width=80, direct=True, show_sender=False)
    in_lines = render_message(incoming, width=80, direct=True, show_sender=False)
    out_plain = "\n".join(line.plain for line in out_lines)
    in_plain = "\n".join(line.plain for line in in_lines)
    assert "Me" not in out_plain
    assert "Ada" not in in_plain
    assert "hello" in out_lines[0].plain
    assert "there" in out_lines[1].plain
    assert "\n\n" not in out_plain
    # Wrapped line starts at the same column as the first line of text.
    hello_col = cell_width(out_lines[0].plain[: out_lines[0].plain.index("hello")])
    there_col = cell_width(out_lines[1].plain[: out_lines[1].plain.index("there")])
    assert hello_col == there_col
    assert _color_at(out_lines[0], "hello") == "#c4b48a"
    assert _color_at(in_lines[0], "hi back") == "#8eabc4"


def test_group_name_shown_once_and_body_indented():
    first = Message(
        account_id=1,
        chat_id=10,
        message_id=1,
        sender_id=7,
        sender_name="Alice",
        plain_text="one\ntwo",
    )
    again = Message(
        account_id=1,
        chat_id=10,
        message_id=2,
        sender_id=7,
        sender_name="Alice",
        plain_text="three",
    )
    other = Message(
        account_id=1,
        chat_id=10,
        message_id=3,
        sender_id=8,
        sender_name="Bob",
        plain_text="four",
    )
    items = [first, DateSeparator(date_str="Monday"), again, UnreadDivider(), other]
    flags = sender_header_flags(items, direct=False)
    assert flags == {1: True, 2: False, 3: True}
    assert sender_header_flags([first, again], direct=True) == {1: False, 2: False}

    first_lines = render_message(first, width=80, show_sender=True)
    again_lines = render_message(again, width=80, show_sender=False)
    other_lines = render_message(other, width=80, show_sender=True)
    assert "Alice" in first_lines[0].plain
    assert "Alice" not in "\n".join(line.plain for line in again_lines)
    assert "Bob" in other_lines[0].plain
    one = next(line.plain for line in first_lines if "one" in line.plain)
    two = next(line.plain for line in first_lines if line.plain.strip() == "two")
    three = next(line.plain for line in again_lines if "three" in line.plain)
    assert one == "      one"
    assert two == "      two"
    assert three == "      three"
    # Group names are colored. The body stays the default text color.
    assert _color_at(first_lines[0], "Alice") not in (None, "#c4b48a")
    assert _color_at(other_lines[0], "Bob") not in (None, "#c4b48a")
    assert _color_at(first_lines[0], "Alice") != _color_at(other_lines[0], "Bob")
    body = next(line for line in first_lines if "one" in line.plain)
    assert _color_at(body, "one") in (None, "default")


def _at(seconds: int) -> datetime:
    return datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def _burst(message_id: int, seconds: int, text: str, *, sender_id: int = 7, name: str = "Ada", outgoing: bool = False) -> Message:
    return Message(
        account_id=1,
        chat_id=10,
        message_id=message_id,
        sender_id=sender_id,
        sender_name=name,
        date=_at(seconds),
        plain_text=text,
        outgoing=outgoing,
    )


def test_bundle_keeps_one_time_within_a_minute_of_the_first():
    first = _burst(1, 0, "one")
    close = _burst(2, 60, "two")
    later = _burst(3, 61, "three")
    other = _burst(4, 70, "four", sender_id=8, name="Bea")
    show_time, gap_after = message_bundle_flags([first, close, later, other])
    assert show_time == {1: True, 2: False, 3: True, 4: True}
    assert gap_after == {1: False, 2: True, 3: True, 4: True}

    # A date line ends the burst, so the next message shows its own time.
    show_time, gap_after = message_bundle_flags([first, DateSeparator(date_str="Monday"), close])
    assert show_time == {1: True, 2: True}
    assert gap_after == {1: True, 2: True}

    head = render_message(first, width=80, direct=True, show_sender=False, show_time=True)
    cont = render_message(close, width=80, direct=True, show_sender=False, show_time=False)
    assert format_time(first.date) in head[0].plain
    assert format_time(close.date) not in "\n".join(line.plain for line in cont)
    one_col = cell_width(head[0].plain[: head[0].plain.index("one")])
    two_col = cell_width(cont[0].plain[: cont[0].plain.index("two")])
    assert one_col == two_col

    group_cont = render_message(close, width=80, show_sender=False, show_time=False)
    assert group_cont[0].plain == "      two"
    assert format_time(close.date) not in group_cont[0].plain


def test_render_chat_row_photo_and_emojis_alignment():
    """Verify that rows with [photo], [file], and multiple emojis (like hand prayers) remain perfectly aligned."""
    import os

    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    chats = [
        Chat(account_id=1, chat_id=1, title="Alice", kind=ChatKind.USER, last_preview="Normal message", last_date=now),
        Chat(account_id=1, chat_id=2, title="Bob", kind=ChatKind.USER, last_preview="[photo]", last_date=now),
        Chat(account_id=1, chat_id=3, title="Charlie", kind=ChatKind.USER, last_preview="[file]", last_date=now),
        Chat(account_id=1, chat_id=4, title="David", kind=ChatKind.USER, last_preview="🙏🙏🙏🙏🙏", last_date=now),
        Chat(account_id=1, chat_id=5, title="Frank", kind=ChatKind.USER, last_preview="[photo] 🙏🙏🙏", last_date=now),
        Chat(account_id=1, chat_id=6, title="Family 👥", kind=ChatKind.GROUP, last_preview="Mom: [photo] Ready!", last_date=now, unread_count=7),
    ]

    for mode in ("1", "2"):
        os.environ["TG_CLI_EMOJI_WIDTH"] = mode
        try:
            for c in chats:
                row = render_chat_row(c, width=80)
                assert cell_width(row.plain) == 80
                time_str = format_chat_time(now)
                time_idx = row.plain.find(time_str)
                assert cell_width(row.plain[:time_idx]) == 69
        finally:
            os.environ.pop("TG_CLI_EMOJI_WIDTH", None)


def test_render_chat_row_student_emoji_titles_alignment():
    """Verify that chat titles with complex emojis (e.g. students 👨‍🎓, students 🎓) do not break preview or timing."""
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    student_chats = [
        Chat(account_id=1, chat_id=1, title="students 🎓", kind=ChatKind.GROUP, last_preview="Assignment due tomorrow", last_date=now),
        Chat(account_id=1, chat_id=2, title="students 👨‍🎓", kind=ChatKind.GROUP, last_preview="Lecture slides uploaded", last_date=now),
        Chat(account_id=1, chat_id=3, title="students 👩‍🎓", kind=ChatKind.GROUP, last_preview="Exam schedule posted", last_date=now),
        Chat(account_id=1, chat_id=4, title="students 👥", kind=ChatKind.GROUP, last_preview="Group project notes", last_date=now),
        Chat(account_id=1, chat_id=5, title="students ✨", kind=ChatKind.GROUP, last_preview="Welcome new members!", last_date=now),
    ]

    for c in student_chats:
        row = render_chat_row(c, width=80)
        assert cell_width(row.plain) == 80
        time_str = format_chat_time(now)
        time_idx = row.plain.find(time_str)
        assert cell_width(row.plain[:time_idx]) == 69
        prev_idx = row.plain.find(c.last_preview)
        assert cell_width(row.plain[:prev_idx]) == 27


def test_slice_by_cells_keeps_a_wide_character_whole():
    assert slice_by_cells("a😀b", 0, 1) == "a"
    assert slice_by_cells("a😀b", 1, 3) == "😀"
    assert slice_by_cells("a😀b", 3, None) == "b"


def test_service_message_shows_the_action():
    message = Message(
        account_id=1,
        chat_id=1,
        message_id=3,
        sender_name="Ada",
        kind=MessageKind.SERVICE,
        plain_text="Ada pinned a message",
    )
    plain = "\n".join(line.plain for line in render_message(message, width=80))
    assert "Ada pinned a message" in plain
    assert plain.strip() != "Ada"

    tagged = "\n".join(
        line.plain for line in render_message(message, width=80, role="admin")
    )
    assert "Ada admin pinned a message" in tagged


def test_group_name_includes_admin_or_owner():
    message = Message(
        account_id=1,
        chat_id=1,
        message_id=4,
        sender_id=7,
        sender_name="Ada",
        plain_text="hello",
    )
    plain = "\n".join(
        line.plain for line in render_message(message, width=80, direct=False, role="owner")
    )
    assert "Ada owner" in plain
    assert "hello" in plain


def test_service_line_does_not_hide_the_next_name():
    first = Message(
        account_id=1, chat_id=1, message_id=1, sender_id=7, sender_name="Ada", plain_text="one"
    )
    service = Message(
        account_id=1,
        chat_id=1,
        message_id=2,
        sender_id=7,
        sender_name="Ada",
        kind=MessageKind.SERVICE,
        plain_text="Ada pinned a message",
    )
    second = Message(
        account_id=1, chat_id=1, message_id=3, sender_id=7, sender_name="Ada", plain_text="two"
    )
    flags = sender_header_flags([first, service, second], direct=False)
    assert flags[1] is True
    assert flags[3] is True






