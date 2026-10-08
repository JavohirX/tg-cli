"""Message composer widget for tg-cli.
Handles multiline text input, 4096-character limit, newlines via Ctrl+J / \\+Enter,
reply/edit context banner, and prevents paste-to-send.
"""

from __future__ import annotations

from typing import Any
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.dom import NoScreen
from textual.events import Key, Paste
from textual.message import Message as TextualMessage
from textual.widget import Widget
from textual.widgets import Static, TextArea


class ComposerInput(TextArea):
    """Subclass of TextArea to give Composer strict control over keys."""

    def check_consume_key(self, key: str, character: str | None = None) -> bool:
        """Leave z/Z for message expand unless the user is actually typing."""
        if key in ("z", "Z"):
            try:
                writing = bool(getattr(self.screen, "is_write_mode", False))
            except NoScreen:
                writing = False
            if not writing:
                return False
        return super().check_consume_key(key, character)

    def on_key(self, event: Key) -> None:
        composer = self.parent
        if not isinstance(composer, Composer):
            return

        if event.key == "enter":
            event.prevent_default()
            event.stop()
            composer.process_enter()
        elif event.key in ("ctrl+j", "shift+enter"):
            event.prevent_default()
            event.stop()
            self.insert("\n")
        elif event.key == "escape":
            event.prevent_default()
            event.stop()
            composer.process_escape()
        elif event.key == "tab":
            event.prevent_default()
            event.stop()
            composer.process_tab()


class Composer(Widget):
    """Composer widget wrapping ComposerInput with Telegram chat rules."""

    DEFAULT_CSS = """
    Composer {
        height: auto;
        min-height: 4;
        max-height: 10;
        background: $surface;
        border-top: solid $primary;
        padding: 0 1;
    }
    Composer #context-bar {
        height: 1;
        width: 100%;
        color: $text-muted;
    }
    Composer ComposerInput {
        height: 3;
        min-height: 2;
        max-height: 8;
        border: none;
        background: $surface;
    }
    Composer ComposerInput:focus {
        border: none;
    }
    """

    class MessageSubmitted(TextualMessage):
        """Posted when user submits a message with Enter."""
        def __init__(self, text: str, reply_to_id: int | None, edit_message_id: int | None) -> None:
            super().__init__()
            self.text = text
            self.reply_to_id = reply_to_id
            self.edit_message_id = edit_message_id

    class CharCountChanged(TextualMessage):
        """Posted when character count changes."""
        def __init__(self, count: int) -> None:
            super().__init__()
            self.count = count

    class ErrorReported(TextualMessage):
        """Posted when user tries to send invalid text (>4096)."""
        def __init__(self, error: str) -> None:
            super().__init__()
            self.error = error

    class CancelRequested(TextualMessage):
        """Posted when Esc is pressed inside composer."""
        def __init__(self) -> None:
            super().__init__()

    BINDINGS = [
        Binding("escape", "handle_escape", "Cancel/Defocus", show=False, priority=True),
        Binding("tab", "switch_to_list", "Switch to List", show=False, priority=True),
    ]

    def __init__(
        self,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.reply_to_id: int | None = None
        self.reply_label: str = ""
        self.edit_message_id: int | None = None
        self.context_bar = Static("", id="context-bar")
        self.text_area = ComposerInput(id="composer-input")

    def compose(self) -> ComposeResult:
        yield self.context_bar
        yield self.text_area

    def on_mount(self) -> None:
        self.context_bar.display = False

    @property
    def text(self) -> str:
        return self.text_area.text

    @text.setter
    def text(self, value: str) -> None:
        self.text_area.text = value
        self.post_message(self.CharCountChanged(len(value)))

    def clear(self) -> None:
        self.text_area.text = ""
        self.clear_context()
        self.post_message(self.CharCountChanged(0))

    def focus_input(self) -> None:
        self.text_area.focus()

    def set_reply_context(self, message_id: int, sender_name: str, preview: str) -> None:
        self.reply_to_id = message_id
        self.reply_label = f"↳ Replying to {sender_name}: '{preview[:30]}' (Esc to cancel)"
        self.edit_message_id = None
        self.context_bar.update(Text(self.reply_label, style="bold cyan"))
        self.context_bar.display = True
        self.focus_input()

    def set_edit_context(self, message_id: int, current_text: str) -> None:
        self.edit_message_id = message_id
        self.reply_to_id = None
        self.context_bar.update(Text(f"✎ Editing message #{message_id} (Esc to cancel)", style="bold yellow"))
        self.context_bar.display = True
        self.text = current_text
        self.focus_input()

    def clear_context(self) -> bool:
        had_context = (self.reply_to_id is not None) or (self.edit_message_id is not None)
        self.reply_to_id = None
        self.edit_message_id = None
        self.context_bar.update("")
        self.context_bar.display = False
        return had_context

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self.post_message(self.CharCountChanged(len(self.text_area.text)))

    def process_enter(self) -> None:
        current_text = self.text_area.text
        if current_text.endswith("\\"):
            # Fallback: remove '\' and insert newline
            self.text_area.text = current_text[:-1] + "\n"
            # Move cursor to end
            self.text_area.cursor_location = (self.text_area.document.line_count - 1, 0)
            return

        clean_text = current_text.strip()
        if not clean_text:
            # Empty enter does nothing
            return

        if len(clean_text) > 4096:
            self.post_message(
                self.ErrorReported(f"Cannot send: {len(clean_text)} chars exceeds 4096 limit")
            )
            return

        text_to_send = clean_text
        reply_id = self.reply_to_id
        edit_id = self.edit_message_id
        self.clear()
        self.post_message(self.MessageSubmitted(text_to_send, reply_id, edit_id))

    def process_escape(self) -> None:
        if self.clear_context():
            return
        self.post_message(self.CancelRequested())

    def process_tab(self) -> None:
        self.post_message(self.CancelRequested())

    def action_handle_escape(self) -> None:
        self.process_escape()

    def action_switch_to_list(self) -> None:
        self.process_tab()
