"""Chat picker for forwarding messages.

Type to narrow the chats, Enter forwards, Tab hides the original sender,
Esc cancels. Only the first matches are listed so 10,000 chats stay fast.
"""

from __future__ import annotations

from typing import Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option
from tg_cli.domain.models import Chat
from tg_cli.domain.render import get_kind_glyph
from tg_cli.ui.paste import PasteInput

PICKER_LIMIT = 50


class ForwardPicker(ModalScreen[tuple[Chat, bool] | None]):
    """Returns (destination chat, hide sender), or None when cancelled."""

    DEFAULT_CSS = """
    ForwardPicker {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }
    #forward-container {
        width: 60;
        height: 20;
        background: $surface;
        border: thick $primary;
        padding: 0 1;
    }
    #forward-title {
        height: 1;
        margin-bottom: 1;
    }
    #forward-input {
        width: 100%;
        margin-bottom: 1;
    }
    #forward-options {
        height: 1fr;
        border: none;
        background: $surface;
    }
    #forward-hint {
        height: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        # Tab would move focus out of the search box; here it is a toggle.
        Binding("tab", "toggle_hide_sender", "Hide Sender", show=False, priority=True),
    ]

    def __init__(self, chats: Sequence[Chat], count: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.chats = list(chats)
        self.count = count
        self.hide_sender = False
        self.matches: list[Chat] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="forward-container"):
            yield Static(f"Forward {self.count} message(s) to...", id="forward-title")
            yield PasteInput(placeholder="Search chats...", id="forward-input")
            yield OptionList(id="forward-options")
            yield Static("", id="forward-hint")

    def on_mount(self) -> None:
        self._filter("")
        self._update_hint()
        self.query_one(Input).focus()

    def _filter(self, query: str) -> None:
        q = query.strip().lower()
        self.matches = []
        for chat in self.chats:
            if not q or q in chat.title.lower():
                self.matches.append(chat)
                if len(self.matches) >= PICKER_LIMIT:
                    break
        options = self.query_one(OptionList)
        options.clear_options()
        for chat in self.matches:
            prompt = Text()
            prompt.append(f"{get_kind_glyph(chat.kind)} ", style="dim")
            prompt.append(chat.title)
            if chat.archived:
                prompt.append("  archived", style="dim")
            options.add_option(Option(prompt))
        if self.matches:
            options.highlighted = 0

    def _update_hint(self) -> None:
        state = "on" if self.hide_sender else "off"
        self.query_one("#forward-hint", Static).update(
            f"Enter forward   Tab hide sender: {state}   Esc cancel"
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        self._filter(event.value)

    def on_key(self, event: Key) -> None:
        options = self.query_one(OptionList)
        if event.key in ("down", "ctrl+n"):
            if options.highlighted is not None:
                options.highlighted = min(len(self.matches) - 1, options.highlighted + 1)
        elif event.key in ("up", "ctrl+p"):
            if options.highlighted is not None:
                options.highlighted = max(0, options.highlighted - 1)
        elif event.key == "enter":
            self._pick(options.highlighted)
        else:
            return
        event.prevent_default()
        event.stop()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._pick(event.option_index)

    def _pick(self, index: int | None) -> None:
        if index is None or not (0 <= index < len(self.matches)):
            return
        self.dismiss((self.matches[index], self.hide_sender))

    def action_toggle_hide_sender(self) -> None:
        self.hide_sender = not self.hide_sender
        self._update_hint()

    def action_cancel(self) -> None:
        self.dismiss(None)
