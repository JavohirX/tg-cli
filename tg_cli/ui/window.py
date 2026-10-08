"""Virtualized windowed list widget for Textual.
Renders only visible items + overscan. Backing data is a sequence of items,
not widgets, allowing 10,000+ items to scroll at 60fps without lag.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Generic, Sequence, TypeVar
from rich.console import Group, RenderableType
from rich.style import Style
from rich.text import Text
from textual.binding import Binding
from textual.events import Click, Key, MouseDown, MouseScrollDown, MouseScrollUp, Resize
from textual.message import Message as TextualMessage
from textual.selection import Selection
from textual.strip import Strip
from textual.widget import Widget
from tg_cli.domain.render import slice_by_cells
from tg_cli.ui.clipboard import copy_text

# A terminal cannot glide between pixels. Moving several lines on each wheel
# notch is what reads as smooth, and it is faster than one row per notch.
WHEEL_LINES = 6

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
        overflow-x: hidden;
        overflow-y: hidden;
        border: none;
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

    class Clicked(TextualMessage):
        """Posted when the list widget is clicked."""
        def __init__(self, index: int | None) -> None:
            super().__init__()
            self.index = index

    class ConfirmationPrompt(TextualMessage):
        """Posted when an action needs y/n confirmation."""
        def __init__(self, prompt: str, on_confirm: Callable[[], None]) -> None:
            super().__init__()
            self.prompt = prompt
            self.on_confirm = on_confirm

    class ReachedTop(TextualMessage):
        """Posted when user attempts to move above the top item."""
        pass

    class TextCopied(TextualMessage):
        """Posted after a selection or row was copied."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

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
        item_height_fn: Callable[[T], int] | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        enable_digit_motion: bool = True,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.enable_digit_motion = enable_digit_motion
        self._items: list[T] = list(items or [])
        self._renderer = renderer or self._default_render
        self._id_fn = id_fn or (lambda item: getattr(item, "identity", id(item)))
        self._is_selectable_fn = is_selectable_fn or (lambda item: True)
        self._item_height_fn = item_height_fn

        self.cursor: int = 0
        self.top_index: int = 0
        # Lines of the top item hidden above the viewport. Lets a tall
        # message scroll inside itself; single-line rows leave this at 0.
        self.top_skip: int = 0
        self.selected_ids: set[Any] = set()
        self.anchor_index: int | None = None
        self.digit_buffer: str = ""
        self._last_click_time: float = 0.0
        self._last_click_idx: int | None = None

        # Find first selectable item
        self._adjust_cursor_to_selectable(1)

    @property
    def items(self) -> list[T]:
        return self._items

    def set_items(self, new_items: Sequence[T], keep_cursor: bool = True) -> None:
        """Update items list, maintaining cursor within valid bounds."""
        self.clear_text_selection()
        self._items = list(new_items)
        self.top_skip = 0
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

    def _get_height(self, index: int) -> int:
        if not (0 <= index < len(self._items)):
            return 1
        if self._item_height_fn is not None:
            try:
                return max(1, self._item_height_fn(self._items[index]))
            except Exception:
                return 1
        return 1

    def _height_from(self, start: int, end: int, limit: int | None = None) -> int:
        """Line count from start through end, honoring top_skip on start.

        Stops once `limit` is reached so a long chat list is not walked whole.
        """
        total = 0
        if start > end:
            return 0
        for idx in range(start, end + 1):
            height = self._get_height(idx)
            if idx == start:
                height = max(0, height - self.top_skip)
            total += height
            if limit is not None and total >= limit:
                return total
        return total

    def _ensure_cursor_visible(self) -> None:
        """Keep the cursor item inside the viewport, taking item heights into account.

        Spare rows are filled with earlier items, so a short last message does
        not leave the history above it off screen.
        """
        if not self._items:
            self.top_index = 0
            self.top_skip = 0
            return

        viewport_height = max(1, self.size.height)
        if self.cursor < self.top_index:
            self.top_index = max(0, self.cursor)
            self.top_skip = 0

        # Reading inside a tall item: keep that offset and don't pull neighbors in.
        if (
            self.top_index == self.cursor
            and self._get_height(self.cursor) > viewport_height
        ):
            max_skip = self._get_height(self.cursor) - viewport_height
            self.top_skip = min(max(0, self.top_skip), max_skip)
            return

        if self._height_from(self.top_index, self.cursor, viewport_height + 1) > viewport_height:
            self.top_skip = 0
            accum = 0
            new_top = self.cursor
            for idx in range(self.cursor, -1, -1):
                height = self._get_height(idx)
                if accum + height > viewport_height and accum > 0:
                    break
                accum += height
                new_top = idx
            self.top_index = max(0, new_top)

        self._fill_viewport_above(viewport_height)

    def _fill_viewport_above(self, viewport_height: int) -> None:
        """Pull earlier rows into unused space without pushing the cursor off."""
        while (
            self._height_from(self.top_index, len(self._items) - 1, viewport_height)
            < viewport_height
        ):
            if self.top_skip > 0:
                used = self._height_from(
                    self.top_index, len(self._items) - 1, viewport_height
                )
                self.top_skip = max(0, self.top_skip - (viewport_height - used))
                continue
            if self.top_index <= 0:
                return
            self.top_index -= 1
            through_cursor = self._height_from(self.top_index, self.cursor)
            if through_cursor > viewport_height:
                self.top_skip = through_cursor - viewport_height
                return

    def _move_cursor(self, delta: int) -> None:
        if not self._items:
            return

        count = len(self._items)
        last_valid_selectable = self.cursor
        curr = self.cursor
        step = 1 if delta > 0 else -1
        remaining = abs(delta)

        while remaining > 0:
            candidate = curr + step
            if not (0 <= candidate < count):
                break
            curr = candidate
            if self._is_selectable(curr):
                last_valid_selectable = curr
                remaining -= 1

        if last_valid_selectable != self.cursor:
            self.clear_text_selection()
            self.cursor = last_valid_selectable
            self.top_skip = 0

            # If anchor is active, update range selection
            if self.anchor_index is not None:
                self._update_range_selection()

            self._ensure_cursor_visible()
            self.post_message(self.CursorMoved(self.cursor, self.get_focused_item()))
            self.refresh()
        elif delta < 0:
            self.post_message(self.ReachedTop())

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

    def _viewport_height(self) -> int:
        return max(1, self.size.height)

    def _advance_lines(self, lines: int) -> None:
        """Hide `lines` more lines above the viewport."""
        left = lines
        count = len(self._items)
        while left > 0 and self.top_index < count:
            height = self._get_height(self.top_index)
            room = height - self.top_skip
            if room <= 0:
                self.top_index += 1
                self.top_skip = 0
                continue
            if left < room:
                self.top_skip += left
                return
            left -= room
            self.top_index += 1
            self.top_skip = 0
        if self.top_index >= count:
            self.top_index = max(0, count - 1)
            self.top_skip = 0

    def _retreat_lines(self, lines: int) -> None:
        """Reveal `lines` that were hidden above the viewport."""
        left = lines
        while left > 0:
            if self.top_skip > 0:
                take = min(self.top_skip, left)
                self.top_skip -= take
                left -= take
                continue
            if self.top_index <= 0:
                return
            self.top_index -= 1
            self.top_skip = self._get_height(self.top_index)

    def _visible_bounds(self) -> tuple[int, int]:
        """First and last item indexes that intersect the viewport."""
        if not self._items:
            return 0, 0
        viewport = self._viewport_height()
        first = self.top_index
        last = self.top_index
        used = 0
        for idx in range(self.top_index, len(self._items)):
            height = self._get_height(idx)
            if idx == self.top_index:
                height = max(0, height - self.top_skip)
            if height <= 0:
                continue
            if used >= viewport:
                break
            last = idx
            used += height
        return first, last

    def _cursor_screen_row(self) -> int:
        """Viewport row where the cursor item begins. The highlight stays here."""
        if not self._items or self.cursor <= self.top_index:
            return 0
        row = 0
        viewport = self._viewport_height()
        for idx in range(self.top_index, self.cursor):
            height = self._get_height(idx)
            if idx == self.top_index:
                height = max(0, height - self.top_skip)
            row += height
            if row >= viewport:
                return viewport - 1
        return min(row, max(0, viewport - 1))

    def _place_cursor_at_row(self, row: int) -> bool:
        """Move the cursor to the selectable item on this viewport row."""
        if not self._items:
            return False
        idx = self._item_at_y(max(0, row))
        first, last = self._visible_bounds()
        if idx is None:
            idx = last
        if not self._is_selectable(idx):
            found: int | None = None
            for step in range(0, last - first + 1):
                for candidate in (idx - step, idx + step):
                    if first <= candidate <= last and self._is_selectable(candidate):
                        found = candidate
                        break
                if found is not None:
                    break
            if found is None:
                return False
            idx = found
        if idx == self.cursor:
            return False
        self.cursor = idx
        if self.anchor_index is not None:
            self._update_range_selection()
        return True

    def _scroll_by_lines(self, delta: int) -> None:
        """Move the viewport by display lines. Positive is down.

        The highlight stays on the same screen row while the rows slide
        under it. j and k still move one item.
        """
        if not self._items or delta == 0:
            return
        self.digit_buffer = ""
        self.clear_text_selection()
        if delta < 0 and self.top_index <= 0 and self.top_skip <= 0:
            self.post_message(self.ReachedTop())
            return
        anchor_row = self._cursor_screen_row()
        viewport = self._viewport_height()
        if delta > 0:
            room = self._height_from(self.top_index, len(self._items) - 1) - viewport
            delta = min(delta, max(0, room))
            if delta <= 0:
                return
            self._advance_lines(delta)
        else:
            self._retreat_lines(-delta)
        if self._place_cursor_at_row(anchor_row):
            self.post_message(self.CursorMoved(self.cursor, self.get_focused_item()))
        self.refresh()

    def _page(self, delta: int) -> None:
        """Scroll by lines. A positive delta moves down one page of the viewport."""
        self._scroll_by_lines(delta)

    def action_half_page_down(self) -> None:
        self._page(max(1, self.size.height // 2))

    def action_half_page_up(self) -> None:
        self._page(-max(1, self.size.height // 2))

    def action_page_down(self) -> None:
        self._page(max(1, self.size.height - 1))

    def action_page_up(self) -> None:
        self._page(-max(1, self.size.height - 1))

    def action_jump_top(self) -> None:
        self.digit_buffer = ""
        self.clear_text_selection()
        self.top_skip = 0
        self.cursor = 0
        self._adjust_cursor_to_selectable(1)
        if self.anchor_index is not None:
            self._update_range_selection()
        self._ensure_cursor_visible()
        self.post_message(self.CursorMoved(self.cursor, self.get_focused_item()))
        self.refresh()

    def action_jump_bottom(self) -> None:
        self.digit_buffer = ""
        self.clear_text_selection()
        self.top_skip = 0
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

        if self.enable_digit_motion:
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
        content_w = self.content_size.width
        viewport_width = max(10, content_w if content_w > 0 else self.size.width)

        if not self._items:
            empty_text = Text("  (No items)", style="dim")
            return Group(empty_text)

        lines: list[RenderableType] = []
        accum_lines = 0
        idx = self.top_index

        while idx < len(self._items) and accum_lines < viewport_height:
            item = self._items[idx]
            is_cursor = (idx == self.cursor)
            is_selected = self._id_fn(item) in self.selected_ids
            rendered_row = self._renderer(item, viewport_width, is_cursor, is_selected)
            h = self._get_height(idx)
            if idx == self.top_index and self.top_skip:
                rendered_row, h = self._crop_top(rendered_row, self.top_skip, h)
            if h <= 0:
                idx += 1
                continue
            lines.append(rendered_row)
            accum_lines += h
            idx += 1

        return Group(*lines)

    def _crop_top(
        self, rendered: RenderableType, skip: int, height: int
    ) -> tuple[RenderableType, int]:
        """Drop the first `skip` lines of a row made of one renderable per line."""
        if skip <= 0:
            return rendered, height
        if isinstance(rendered, Group):
            parts = list(rendered.renderables)[skip:]
            if not parts:
                return Text(""), 0
            return Group(*parts), len(parts)
        if skip >= height:
            return Text(""), 0
        return rendered, height

    def on_resize(self, event: Resize) -> None:
        self._ensure_cursor_visible()
        self.refresh()

    def _item_at_y(self, y: int) -> int | None:
        """Item index at a viewport row, or None when y is past the content."""
        accum = 0
        for idx in range(self.top_index, len(self._items)):
            height = self._get_height(idx)
            if idx == self.top_index:
                height = max(0, height - self.top_skip)
            if height <= 0:
                continue
            if accum <= y < accum + height:
                return idx
            accum += height
            if accum > y:
                break
        return None

    def _has_text_selection(self) -> bool:
        try:
            return self.text_selection is not None
        except Exception:
            return False

    def clear_text_selection(self) -> bool:
        """Clear a drag selection in this list. Return True when there was one."""
        if not self._has_text_selection():
            return False
        try:
            self.screen.clear_selection()
        except Exception:
            return False
        return True

    def _copy_source(self, item: T | None) -> str:
        """Text a right-click copies when nothing is dragged."""
        if item is None:
            return ""
        plain = getattr(item, "plain_text", None)
        if isinstance(plain, str) and plain.strip():
            return plain
        title = getattr(item, "title", None)
        if isinstance(title, str) and title:
            preview = getattr(item, "last_preview", "") or ""
            if preview:
                return f"{title}\n{preview}".strip()
            return title
        date_str = getattr(item, "date_str", None)
        if isinstance(date_str, str):
            return date_str
        return ""

    def _copy(self, text: str) -> None:
        if not text.strip():
            return
        try:
            copied = copy_text(self.app, text)
        except Exception:
            copied = False
        if copied:
            self.post_message(self.TextCopied(text))

    def on_mouse_down(self, event: MouseDown) -> None:
        """Right-click copies the drag selection, or the message under the pointer."""
        if event.button != 3:
            return
        selected = ""
        try:
            selected = self.screen.get_selected_text() or ""
        except Exception:
            selected = ""
        if selected.strip():
            self._copy(selected)
            return
        item = None
        idx = self._item_at_y(event.y)
        if idx is not None:
            item = self._items[idx]
        self._copy(self._copy_source(item))

    def on_click(self, event: Click) -> None:
        if event.button == 3:
            return
        if not self._items:
            self.focus()
            return

        self.focus()
        # A drag just finished. Leave the viewport so the highlight stays put.
        if self._has_text_selection():
            return

        clicked_idx = self._item_at_y(event.y)
        self.post_message(self.Clicked(clicked_idx))

        if clicked_idx is None or not self._is_selectable(clicked_idx):
            return

        if clicked_idx != self.cursor:
            self.top_skip = 0
        self.cursor = clicked_idx
        self._ensure_cursor_visible()
        self.post_message(self.CursorMoved(self.cursor, self.get_focused_item()))

        now = time.time()
        if (
            self._last_click_idx == clicked_idx
            and (now - self._last_click_time) < 0.4
        ):
            self._last_click_time = 0.0
            self._last_click_idx = None
            self.post_message(self.ItemActivated(self.get_focused_item(), self.cursor))
        else:
            self._last_click_time = now
            self._last_click_idx = clicked_idx

        self.refresh()

    def _selection_span(self, y: int) -> tuple[int, int] | None:
        if not self._has_text_selection():
            return None
        try:
            selection = self.text_selection
        except Exception:
            return None
        if selection is None:
            return None
        return selection.get_span(y)

    def _selection_style(self) -> Style:
        cached = getattr(self, "_selection_style_cache", None)
        if cached is not None:
            return cached
        try:
            style = Style.from_styles(self.screen.get_component_styles("screen--selection"))
        except Exception:
            style = Style(color="black", bgcolor="#c4b48a")
        self._selection_style_cache = style
        return style

    def selection_updated(self, selection: Selection | None) -> None:
        self._selection_style_cache = None
        super().selection_updated(selection)

    def render_line(self, y: int) -> Strip:
        line = super().render_line(y)
        span = self._selection_span(y)
        if span is None:
            return line
        return _paint_selection(line, span[0], span[1], self._selection_style())

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Text under a drag, in cell columns, from the lines on screen."""
        height = self._viewport_height()
        pieces: list[str] = []
        saw = False
        for y in range(height):
            span = selection.get_span(y)
            if span is None:
                continue
            saw = True
            start, end = span
            end_cell = None if end < 0 else end
            # Read the unhighlighted line so a style cannot change the text.
            plain = super().render_line(y).text
            pieces.append(slice_by_cells(plain, start, end_cell).rstrip())
        if not saw:
            return None
        text = "\n".join(pieces).strip("\n")
        if not text.strip():
            return None
        return text, "\n"

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        event.stop()
        self._scroll_by_lines(WHEEL_LINES)

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        event.stop()
        self._scroll_by_lines(-WHEEL_LINES)


def _paint_selection(strip: Strip, start: int, end: int, style: Style) -> Strip:
    """Paint the selected cell range. `end < 0` means the rest of the line."""
    width = strip.cell_length
    if end < 0 or end > width:
        end = width
    start = max(0, min(start, width))
    if start >= end:
        return strip
    left = strip.crop(0, start)
    mid = strip.crop(start, end).apply_style(style)
    right = strip.crop(end, width)
    return Strip.join([left, mid, right])
