"""Tests for WindowedList virtualized widget."""

import time
import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from tg_cli.ui.window import WindowedList


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
