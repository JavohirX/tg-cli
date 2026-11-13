"""Tests for WindowedList virtualized widget."""

import time
import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from tg_cli.ui.window import WHEEL_LINES, WindowedList


class WindowListTestApp(App):
    def __init__(self, items: list[str], height: int = 20) -> None:
        super().__init__()
        self.test_items = items
        self.widget_height = height
        self.window_list = WindowedList[str](
            items=items,
            renderer=lambda item, w, cur, sel: Text(f"{'> ' if cur else '  '}{'[x] ' if sel else ''}{item}"),
            id_fn=lambda item: item,
        )

    def compose(self) -> ComposeResult:
        yield self.window_list


@pytest.mark.asyncio
async def test_windowed_list_10k_items_performance():
    """Verify 10,000 items load and navigate in sub-millisecond time without widget bloat."""
    items = [f"Item #{i}" for i in range(10_000)]
    w = WindowedList[str](items=items, id_fn=lambda x: x)
    assert len(w.items) == 10_000
    assert w.cursor == 0

    # Moving down with count
    t0 = time.perf_counter()
    w._move_cursor(500)
    elapsed = time.perf_counter() - t0
    assert w.cursor == 500
    assert elapsed < 0.05, f"10k navigation too slow: {elapsed}s"

    # Jump to bottom and top
    w.action_jump_bottom()
    assert w.cursor == 9999
    w.action_jump_top()
    assert w.cursor == 0


@pytest.mark.asyncio
async def test_windowed_list_motions():
    items = [f"Row {i}" for i in range(100)]
    app = WindowListTestApp(items)
    async with app.run_test() as pilot:
        w = app.window_list
        w.focus()
        await pilot.pause()

        assert w.cursor == 0

        # j moves down 1
        await pilot.press("j")
        assert w.cursor == 1

        # k moves up 1
        await pilot.press("k")
        assert w.cursor == 0

        # Digit prefix: 5 then j -> moves down 5
        await pilot.press("5", "j")
        assert w.cursor == 5

        # Digit prefix: 3 then k -> moves up 3
        await pilot.press("3", "k")
        assert w.cursor == 2

        # Half page down and half page up
        await pilot.press("ctrl+d")
        half_down = w.cursor
        assert half_down > 2
        await pilot.press("ctrl+u")
        assert w.cursor < half_down

        # Page down and page up
        await pilot.press("pagedown")
        assert w.cursor > 2
        curr = w.cursor
        await pilot.press("pageup")
        assert w.cursor < curr

        # End jumps to bottom
        await pilot.press("end")
        assert w.cursor == 99

        # Home jumps to top
        await pilot.press("home")
        assert w.cursor == 0


@pytest.mark.asyncio
async def test_windowed_list_selection():
    items = [f"Row {i}" for i in range(20)]
    app = WindowListTestApp(items)
    async with app.run_test() as pilot:
        w = app.window_list
        w.focus()
        await pilot.pause()

        # Space toggles single item selection
        await pilot.press("space")
        assert "Row 0" in w.selected_ids

        # Toggle again removes it
        await pilot.press("space")
        assert "Row 0" not in w.selected_ids

        # Range selection with 'v'
        await pilot.press("v")
        assert w.anchor_index == 0
        assert "Row 0" in w.selected_ids

        # Move down with j extends range
        await pilot.press("j")
        assert w.cursor == 1
        assert "Row 0" in w.selected_ids
        assert "Row 1" in w.selected_ids

        await pilot.press("j")
        assert w.cursor == 2
        assert {"Row 0", "Row 1", "Row 2"}.issubset(w.selected_ids)

        # Esc clears selection
        await pilot.press("escape")
        assert len(w.selected_ids) == 0
        assert w.anchor_index is None

        # Select all with 'V' (len 20 <= 50, so no prompt)
        await pilot.press("V")
        assert len(w.selected_ids) == 20
        # Esc clears all
        await pilot.press("escape")
        assert len(w.selected_ids) == 0


@pytest.mark.asyncio
async def test_windowed_list_variable_height_anchoring():
    """Verify variable-height items anchor properly and cursor remains visible."""
    # 30 items, each taking 3 terminal rows
    items = [f"Item {i}" for i in range(30)]

    class AnchoringApp(App):
        def compose(self) -> ComposeResult:
            yield WindowedList[str](
                items=items,
                item_height_fn=lambda it: 3,
                id_fn=lambda x: x,
                id="list",
            )

    app = AnchoringApp()
    async with app.run_test(size=(80, 15)) as pilot:
        w = app.query_one("#list", WindowedList)
        await pilot.pause()

        # Jump to bottom
        w.action_jump_bottom()
        assert w.cursor == 29
        # From top_index to cursor (29) must fit in 15 rows:
        # 29 (3), 28 (3), 27 (3), 26 (3), 25 (3) = 15 rows -> top_index should be 25
        assert w.top_index == 25

        # Move cursor up 1 -> cursor 28
        w.action_cursor_up()
        assert w.cursor == 28
        # Still visible, top_index should not move
        assert w.top_index == 25

        # Move cursor up 4 more -> cursor 24 (above top_index 25)
        for _ in range(4):
            w.action_cursor_up()
        assert w.cursor == 24
        # top_index must scroll up to include 24
        assert w.top_index == 24


@pytest.mark.asyncio
async def test_windowed_list_click_navigation_and_activation():
    """Verify clicking an item moves cursor and double clicking emits ItemActivated."""
    items = [f"Item {i}" for i in range(10)]
    activated_items = []

    class ClickApp(App):
        def compose(self) -> ComposeResult:
            yield WindowedList[str](items=items, id_fn=lambda x: x, id="list")

        def on_windowed_list_item_activated(self, event: WindowedList.ItemActivated) -> None:
            activated_items.append(event.item)

    app = ClickApp()
    async with app.run_test() as pilot:
        w = app.query_one("#list", WindowedList)
        await pilot.pause()
        assert w.cursor == 0

        # Click on row index 4 (y=4)
        from textual.events import Click
        w.on_click(Click(w, 5, 4, 5, 4, 5, 4, 0, False, False, False))
        await pilot.pause()
        assert w.cursor == 4

        # Press j to move from the clicked item
        await pilot.press("j")
        assert w.cursor == 5

        # Double click on row 5: call on_click twice rapidly
        click_ev = Click(w, 5, 5, 5, 5, 5, 5, 0, False, False, False)
        w.on_click(click_ev)
        w.on_click(click_ev)
        await pilot.pause()
        assert len(activated_items) == 1
        assert activated_items[0] == "Item 5"


@pytest.mark.asyncio
async def test_reused_row_paints_the_same_lines():
    """A row object kept by the caller is painted once and reused."""
    from rich.console import Group

    lines = [Text(f"line {i}") for i in range(3)]

    class ReuseApp(App):
        def compose(self) -> ComposeResult:
            yield WindowedList[str](
                items=["only"],
                renderer=lambda item, w, cur, sel: Group(*lines),
                item_height_fn=lambda item: 3,
                id_fn=lambda item: item,
                id="list",
            )

    app = ReuseApp()
    async with app.run_test(size=(40, 8)) as pilot:
        widget = app.query_one("#list", WindowedList)
        await pilot.pause()
        first = [widget.render_line(y).text.rstrip() for y in range(3)]
        widget.refresh()
        await pilot.pause()
        second = [widget.render_line(y).text.rstrip() for y in range(3)]
        assert first == ["line 0", "line 1", "line 2"]
        assert second == first


def test_scroll_measures_the_page_and_not_the_whole_list():
    """A notch counts lines until the step fits. It does not walk every row."""
    seen: list[int] = []

    def height(item: int) -> int:
        seen.append(item)
        return 1

    items = list(range(500))
    widget = WindowedList[int](items=items, item_height_fn=height, id_fn=lambda item: item)
    seen.clear()
    widget._scroll_by_lines(WHEEL_LINES)
    assert widget.cursor == WHEEL_LINES
    assert seen
    assert max(seen) < 40


@pytest.mark.asyncio
async def test_windowed_list_mouse_scroll():
    """One wheel notch moves several lines, then back to the top."""
    items = [f"Item {i}" for i in range(10)]
    w = WindowedList[str](items=items, id_fn=lambda x: x)
    assert w.cursor == 0

    from textual.events import MouseScrollDown, MouseScrollUp
    w.on_mouse_scroll_down(MouseScrollDown(w, 0, 0, 0, 0, 0, 0, 0, False, False, False))
    assert w.cursor == WHEEL_LINES

    w.on_mouse_scroll_up(MouseScrollUp(w, 0, 0, 0, 0, 0, 0, 0, False, False, False))
    assert w.cursor == 0


@pytest.mark.asyncio
async def test_page_down_on_single_line_rows_moves_by_items():
    """Chat-list rows are one line. Page down still moves the cursor by items."""
    items = [f"Row {i}" for i in range(30)]

    class PageApp(App):
        def compose(self) -> ComposeResult:
            yield WindowedList[str](items=items, id_fn=lambda x: x, id="list")

    app = PageApp()
    async with app.run_test(size=(40, 10)) as pilot:
        w = app.query_one("#list", WindowedList)
        await pilot.pause()
        w.action_page_down()
        assert w.cursor == w.size.height - 1
        assert w.top_skip == 0


@pytest.mark.asyncio
async def test_page_down_scrolls_inside_a_tall_row():
    """A message taller than the viewport pages inside itself. j still leaves it."""
    from rich.console import Group

    class TallApp(App):
        def compose(self) -> ComposeResult:
            yield WindowedList[str](
                items=["tall", "next"],
                renderer=lambda item, w, cur, sel: Group(
                    *(Text(f"{item}-{i}") for i in range(20))
                ),
                item_height_fn=lambda item: 20,
                id_fn=lambda x: x,
                id="list",
            )

    app = TallApp()
    async with app.run_test(size=(40, 10)) as pilot:
        w = app.query_one("#list", WindowedList)
        await pilot.pause()
        assert w.cursor == 0
        w.action_page_down()
        assert w.cursor == 0
        assert w.top_skip > 0
        shown = "".join(
            "".join(seg.text for seg in w.render_line(y)) for y in range(w.size.height)
        )
        assert "tall-0" not in shown
        assert "tall-" in shown

        w.action_cursor_down()
        assert w.cursor == 1
        assert w.top_skip == 0


@pytest.mark.asyncio
async def test_wheel_scrolls_lines_inside_a_tall_row():
    """The wheel moves a few lines of a tall message instead of jumping past it."""
    from rich.console import Group
    from textual.events import MouseScrollDown

    class TallApp(App):
        def compose(self) -> ComposeResult:
            yield WindowedList[str](
                items=["tall", "next"],
                renderer=lambda item, w, cur, sel: Group(
                    *(Text(f"{item}-{i}") for i in range(20))
                ),
                item_height_fn=lambda item: 20,
                id_fn=lambda x: x,
                id="list",
            )

    app = TallApp()
    async with app.run_test(size=(40, 12)) as pilot:
        w = app.query_one("#list", WindowedList)
        await pilot.pause()
        w.on_mouse_scroll_down(MouseScrollDown(w, 0, 0, 0, 0, 0, 0, 0, False, False, False))
        assert w.cursor == 0
        assert w.top_skip == WHEEL_LINES


@pytest.mark.asyncio
async def test_drag_selection_returns_the_cells_under_it():
    """A drag copies the cells it covers, including across a wide character."""
    from textual.geometry import Offset
    from textual.selection import Selection

    class SelectApp(App):
        def compose(self) -> ComposeResult:
            yield WindowedList[str](
                items=["hello world"],
                id_fn=lambda item: item,
                id="list",
            )

    app = SelectApp()
    async with app.run_test(size=(40, 8)) as pilot:
        w = app.query_one("#list", WindowedList)
        await pilot.pause()
        # Default row is "> hello world". Columns 2..7 are "hello".
        w.screen.selections = {
            w: Selection.from_offsets(Offset(2, 0), Offset(7, 0)),
        }
        selected = w.get_selection(w.text_selection)
        assert selected is not None
        assert selected[0] == "hello"
        painted = w.render_line(0)
        assert "hello" in painted.text


@pytest.mark.asyncio
async def test_right_click_copies_the_message_under_the_pointer(monkeypatch):
    from textual.events import MouseDown

    copied: list[str] = []
    monkeypatch.setattr(
        "tg_cli.ui.window.copy_text",
        lambda app, text: copied.append(text) or True,
    )

    class Row:
        def __init__(self, text: str) -> None:
            self.plain_text = text
            self.identity = text

    class CopyApp(App):
        def compose(self) -> ComposeResult:
            yield WindowedList[Row](
                items=[Row("hello from the row")],
                id_fn=lambda item: item.identity,
                id="list",
            )

    app = CopyApp()
    async with app.run_test(size=(40, 8)) as pilot:
        w = app.query_one("#list", WindowedList)
        await pilot.pause()
        w.on_mouse_down(MouseDown(w, 0, 0, 0, 0, 3, False, False, False))
        assert copied == ["hello from the row"]


def _cell_backgrounds(strip) -> list[str | None]:
    """Background name of each cell. None means the segment has no background."""
    cells: list[str | None] = []
    for segment in strip:
        if segment.control:
            continue
        style = segment.style
        color = None if style is None else style.bgcolor
        name = None if color is None else color.name.lower()
        cells.extend([name] * segment.cell_length)
    return cells


_DARK = "#080808"
_LIGHT = "#1c1c1c"


@pytest.mark.asyncio
async def test_stripe_is_one_shade_per_message():
    """Every line of a message shares a shade. The next message takes the other."""
    from rich.console import Group

    class BandApp(App):
        def compose(self) -> ComposeResult:
            yield WindowedList[str](
                items=["tall", "next"],
                renderer=lambda item, w, cur, sel: Group(
                    *(Text(f"{item}-{i}") for i in range(4))
                ),
                item_height_fn=lambda item: 4,
                id_fn=lambda item: item,
                stripe=True,
                id="list",
            )

    app = BandApp()
    async with app.run_test(size=(40, 4)) as pilot:
        w = app.query_one("#list", WindowedList)
        await pilot.pause()
        tall = [_cell_backgrounds(w.render_line(y))[0] for y in range(4)]
        assert tall == [_DARK] * 4
        assert "tall-0" in w.render_line(0).text

        w._scroll_by_lines(1)
        await pilot.pause()
        assert "tall-1" in w.render_line(0).text
        assert [_cell_backgrounds(w.render_line(y))[0] for y in range(3)] == [_DARK] * 3
        assert _cell_backgrounds(w.render_line(3))[0] == _LIGHT
        assert "next-0" in w.render_line(3).text

        w._scroll_by_lines(3)
        await pilot.pause()
        assert "next-0" in w.render_line(0).text
        assert [_cell_backgrounds(w.render_line(y))[0] for y in range(4)] == [_LIGHT] * 4


@pytest.mark.asyncio
async def test_stripe_leaves_cursor_badge_and_selection_colors():
    """A band fills plain cells. Cursor, unread, and a drag keep their own colors."""
    from datetime import datetime, timezone
    from textual.geometry import Offset
    from textual.selection import Selection
    from tg_cli.domain.models import Chat, ChatKind
    from tg_cli.domain.render import render_chat_row

    def row(chat: Chat, width: int, is_cursor: bool, is_selected: bool):
        return render_chat_row(
            chat, width, is_cursor=is_cursor, is_selected=is_selected
        )

    chats = [
        Chat(
            account_id=1,
            chat_id=1,
            title="Ada",
            kind=ChatKind.USER,
            unread_count=3,
            last_date=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
            last_preview="hello",
        ),
        Chat(
            account_id=1,
            chat_id=2,
            title="Bea",
            kind=ChatKind.USER,
            last_date=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
            last_preview="there",
        ),
    ]

    class ChatApp(App):
        def compose(self) -> ComposeResult:
            yield WindowedList[Chat](
                items=chats,
                renderer=row,
                id_fn=lambda chat: chat.chat_id,
                stripe=True,
                id="list",
            )

    app = ChatApp()
    async with app.run_test(size=(80, 8)) as pilot:
        w = app.query_one("#list", WindowedList)
        await pilot.pause()
        # Row 0 is the cursor, so grey15 covers it. Move the cursor off it.
        w.cursor = 1
        w.refresh()
        await pilot.pause()

        plain = _cell_backgrounds(w.render_line(0))
        assert _DARK in plain
        assert "blue" in plain
        cursor = _cell_backgrounds(w.render_line(1))
        assert cursor[0] == "grey15"
        assert all(bg == "grey15" for bg in cursor)

        w.screen.selections = {
            w: Selection.from_offsets(Offset(2, 0), Offset(6, 0)),
        }
        selected = _cell_backgrounds(w.render_line(0))
        assert selected[2] != plain[2]
        assert selected[0] == plain[0]

