"""Virtualized windowed list widget for Textual.
Renders only visible items + overscan. Backing data is a sequence of items,
not widgets, allowing 10,000+ items to scroll at 60fps without lag.
"""

from __future__ import annotations

from typing import Any, Callable, Generic, Sequence, TypeVar
from rich.console import Group, RenderableType
from rich.text import Text
from textual.binding import Binding
from textual.events import Key, Resize
from textual.message import Message as TextualMessage
from textual.widget import Widget

T = TypeVar("T")


class WindowedList(Widget, Generic[T]):
    """High-performance windowed list widget for Textual."""

    can_focus = True

    DEFAULT_CSS = """
    WindowedList {
        width: 100%;
        height: 100%;
        background: $background;
        color: $text;
        overflow-y: hidden;
    }
    WindowedList:focus {
        border-right: heavy $accent;
    }
    """

    class ItemActivated(TextualMessage):
        """Posted when Enter is pressed on the focused item."""
        def __init__(self, item: Any, index: int) -> None:
            super().__init__()
            self.item = item
            self.index = index

    class CursorMoved(TextualMessage):
        """Posted when cursor position changes."""
        def __init__(self, index: int, item: Any | None) -> None:
            super().__init__()
            self.index = index
            self.item = item

    class SelectionUpdated(TextualMessage):
        """Posted when selection set changes."""
        def __init__(self, selected_ids: set[Any]) -> None:
            super().__init__()
            self.selected_ids = selected_ids

    class ConfirmationPrompt(TextualMessage):
        """Posted when an action needs y/n confirmation."""
        def __init__(self, prompt: str, on_confirm: Callable[[], None]) -> None:
            super().__init__()
            self.prompt = prompt
            self.on_confirm = on_confirm

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("ctrl+d", "half_page_down", "Half Page Down", show=False),
        Binding("ctrl+u", "half_page_up", "Half Page Up", show=False),
        Binding("pagedown", "page_down", "Page Down", show=False),
        Binding("pageup", "page_up", "Page Up", show=False),
        Binding("home", "jump_top", "Top", show=False),
        Binding("end", "jump_bottom", "Bottom", show=False),
        Binding("v", "toggle_anchor", "Range Select", show=False),
        Binding("space", "toggle_select", "Toggle", show=False),
        Binding("V", "select_all", "Select All", show=False),
        Binding("enter", "activate_current", "Open", show=False),
    ]

    def __init__(
        self,
        items: Sequence[T] | None = None,
        renderer: Callable[[T, int, bool, bool], RenderableType] | None = None,
        id_fn: Callable[[T], Any] | None = None,
        is_selectable_fn: Callable[[T], bool] | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._items: list[T] = list(items or [])
        self._renderer = renderer or self._default_render
        self._id_fn = id_fn or (lambda item: getattr(item, "identity", id(item)))
        self._is_selectable_fn = is_selectable_fn or (lambda item: True)

        self.cursor: int = 0
        self.top_index: int = 0
        self.selected_ids: set[Any] = set()
        self.anchor_index: int | None = None
        self.digit_buffer: str = ""

        # Find first selectable item
        self._adjust_cursor_to_selectable(1)

    @property
    def items(self) -> list[T]:
        return self._items

    def set_items(self, new_items: Sequence[T], keep_cursor: bool = True) -> None:
        """Update items list, maintaining cursor within valid bounds."""
        self._items = list(new_items)
        if not self._items:
            self.cursor = 0
            self.top_index = 0
            self.clear_selection()
            self.refresh()
            return

        if not keep_cursor or self.cursor >= len(self._items):
            self.cursor = 0
            self._adjust_cursor_to_selectable(1)

        self._ensure_cursor_visible()
        self.refresh()

    def get_selected_items(self) -> list[T]:
        """Return list of currently selected items in order."""
        if not self.selected_ids:
            return []
        return [item for item in self._items if self._id_fn(item) in self.selected_ids]

    def get_focused_item(self) -> T | None:
        if 0 <= self.cursor < len(self._items):
            return self._items[self.cursor]
        return None

    def clear_selection(self) -> bool:
        """Clear all selections and range anchor. Returns True if there was selection."""
        had_selection = bool(self.selected_ids or self.anchor_index is not None)
        self.selected_ids.clear()
        self.anchor_index = None
        if had_selection:
            self.post_message(self.SelectionUpdated(set()))
            self.refresh()
        return had_selection

    def _default_render(
        self, item: T, width: int, is_cursor: bool, is_selected: bool
    ) -> RenderableType:
        prefix = "> " if is_cursor else "  "
        if is_selected:
            prefix += "[x] "
        return Text(f"{prefix}{item}")

    def _is_selectable(self, index: int) -> bool:
        if 0 <= index < len(self._items):
            return self._is_selectable_fn(self._items[index])
        return False

    def _adjust_cursor_to_selectable(self, direction: int = 1) -> None:
        """Ensure cursor rests on a selectable item."""
        if not self._items:
            self.cursor = 0
            return

        count = len(self._items)
        curr = max(0, min(self.cursor, count - 1))
        step = 1 if direction >= 0 else -1

        idx = curr
        while 0 <= idx < count:
            if self._is_selectable(idx):
                self.cursor = idx
                return
            idx += step

        # If not found in primary direction, try reverse direction
        idx = curr - step
        while 0 <= idx < count:
            if self._is_selectable(idx):
                self.cursor = idx
                return
            idx -= step

    def _ensure_cursor_visible(self) -> None:
        """Keep the cursor line inside the viewport with 1 row of overscan."""
        viewport_height = max(1, self.size.height)
        if self.cursor < self.top_index:
            self.top_index = max(0, self.cursor)
        elif self.cursor >= self.top_index + viewport_height:
            self.top_index = max(0, self.cursor - viewport_height + 1)

    def _move_cursor(self, delta: int) -> None:
        if not self._items:
            return

        count = len(self._items)
        new_cursor = self.cursor
        step = 1 if delta > 0 else -1
        remaining = abs(delta)

        while remaining > 0:
            candidate = new_cursor + step
            if not (0 <= candidate < count):
                break
            new_cursor = candidate
            if self._is_selectable(new_cursor):
                remaining -= 1

        if new_cursor != self.cursor:
            self.cursor = new_cursor

            # If anchor is active, update range selection
            if self.anchor_index is not None:
                self._update_range_selection()

            self._ensure_cursor_visible()
            self.post_message(self.CursorMoved(self.cursor, self.get_focused_item()))
            self.refresh()

    def _update_range_selection(self) -> None:
        if self.anchor_index is None:
            return
        start = min(self.anchor_index, self.cursor)
        end = max(self.anchor_index, self.cursor)
        self.selected_ids.clear()
        for i in range(start, end + 1):
            if self._is_selectable(i):
                self.selected_ids.add(self._id_fn(self._items[i]))
        self.post_message(self.SelectionUpdated(set(self.selected_ids)))

    def _get_count_prefix(self) -> int:
        if self.digit_buffer:
            try:
                count = max(1, int(self.digit_buffer))
            except ValueError:
                count = 1
            self.digit_buffer = ""
            return count
        return 1

    # Key actions
    def action_cursor_down(self) -> None:
        count = self._get_count_prefix()
        self._move_cursor(count)

    def action_cursor_up(self) -> None:
        count = self._get_count_prefix()
        self._move_cursor(-count)

    def action_half_page_down(self) -> None:
        self.digit_buffer = ""
        delta = max(1, self.size.height // 2)
        self._move_cursor(delta)

    def action_half_page_up(self) -> None:
        self.digit_buffer = ""
        delta = max(1, self.size.height // 2)
        self._move_cursor(-delta)

    def action_page_down(self) -> None:
        self.digit_buffer = ""
        delta = max(1, self.size.height - 1)
        self._move_cursor(delta)

    def action_page_up(self) -> None:
        self.digit_buffer = ""
        delta = max(1, self.size.height - 1)
        self._move_cursor(-delta)

    def action_jump_top(self) -> None:
        self.digit_buffer = ""
        self.cursor = 0
        self._adjust_cursor_to_selectable(1)
        if self.anchor_index is not None:
            self._update_range_selection()
        self._ensure_cursor_visible()
        self.post_message(self.CursorMoved(self.cursor, self.get_focused_item()))
        self.refresh()

    def action_jump_bottom(self) -> None:
        self.digit_buffer = ""
        if self._items:
            self.cursor = len(self._items) - 1
            self._adjust_cursor_to_selectable(-1)
            if self.anchor_index is not None:
                self._update_range_selection()
            self._ensure_cursor_visible()
            self.post_message(self.CursorMoved(self.cursor, self.get_focused_item()))
            self.refresh()

    def action_toggle_anchor(self) -> None:
        """'v' sets anchor. If anchor already set, clears anchor."""
        self.digit_buffer = ""
        if self.anchor_index is None:
            self.anchor_index = self.cursor
            # Select current item as anchor
            if self._is_selectable(self.cursor):
                self.selected_ids.add(self._id_fn(self._items[self.cursor]))
                self.post_message(self.SelectionUpdated(set(self.selected_ids)))
        else:
            self.anchor_index = None
        self.refresh()

    def action_toggle_select(self) -> None:
        """Space toggles selection of current item."""
        self.digit_buffer = ""
        self.anchor_index = None  # manual toggle cancels range anchor
        item = self.get_focused_item()
        if item is not None and self._is_selectable(self.cursor):
            item_id = self._id_fn(item)
            if item_id in self.selected_ids:
                self.selected_ids.remove(item_id)
            else:
                self.selected_ids.add(item_id)
            self.post_message(self.SelectionUpdated(set(self.selected_ids)))
            self.refresh()

    def action_select_all(self) -> None:
        """'V' selects all items in view (confirms if > 50)."""
        self.digit_buffer = ""
        selectable_items = [item for i, item in enumerate(self._items) if self._is_selectable(i)]
        count = len(selectable_items)
        if count == 0:
            return

        def do_select() -> None:
            self.selected_ids = {self._id_fn(item) for item in selectable_items}
            self.anchor_index = None
            self.post_message(self.SelectionUpdated(set(self.selected_ids)))
            self.refresh()

        if count > 50:
            self.post_message(
                self.ConfirmationPrompt(
                    prompt=f"Select all {count} chats? (y/n)",
                    on_confirm=do_select,
                )
            )
        else:
            do_select()

    def action_activate_current(self) -> None:
        item = self.get_focused_item()
        if item is not None and self._is_selectable(self.cursor):
            self.post_message(self.ItemActivated(item, self.cursor))

    def on_key(self, event: Key) -> None:
        """Handle digit prefixes for motions and selection clearing on Escape."""
        if event.key == "escape":
            self.digit_buffer = ""
            if self.clear_selection():
                event.prevent_default()
                event.stop()
                return

        char = event.character
        if char is not None and char.isdigit():
            # Don't allow starting with 0
            if char == "0" and not self.digit_buffer:
                return
            self.digit_buffer += char
            event.prevent_default()
            event.stop()
        elif event.key not in ("j", "k", "down", "up"):
            self.digit_buffer = ""

    def render(self) -> RenderableType:
        """Render viewport height lines into a Group."""
        viewport_height = max(1, self.size.height)
        viewport_width = max(10, self.size.width)

        if not self._items:
            empty_text = Text("  (No items)", style="dim")
            return Group(empty_text)

        lines: list[RenderableType] = []
        end_idx = min(len(self._items), self.top_index + viewport_height)

        for idx in range(self.top_index, end_idx):
            item = self._items[idx]
            is_cursor = (idx == self.cursor)
            is_selected = self._id_fn(item) in self.selected_ids
            rendered_row = self._renderer(item, viewport_width, is_cursor, is_selected)
            lines.append(rendered_row)

        return Group(*lines)
