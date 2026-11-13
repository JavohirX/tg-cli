"""Paste text into any field, and pasted files into the open chat.

Ctrl+V on Windows arrives as a key. Textual's paste action then reads its
own in-process clipboard, which is empty when the text was copied from
another program. This reads the system clipboard instead.

A paste that is only photos and videos asks whether to send them as
pictures or as files, and sends the group as an album. A paste that mixes
in any other file is sent as files with no question.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.events import Paste
from textual.screen import ModalScreen
from textual.widgets import Input, Static, TextArea

from tg_cli.domain.files import describe_media_group
from tg_cli.ui.clipboard import ClipboardPayload, read_clipboard

_QUOTED_PATH = re.compile(r'"([^"\r\n]+)"')
_PASTE_WINDOW_SECONDS = 0.3


class PasteInput(Input):
    """Input that pastes from the system clipboard, including passwords."""

    BINDINGS = [
        Binding("shift+insert,ctrl+shift+v", "paste", "Paste", show=False),
    ]

    def action_paste(self) -> None:
        deliver_system_paste(self.app)

    def _on_paste(self, event: Paste) -> None:
        event.prevent_default()
        event.stop()
        deliver_system_paste(self.app, explicit_text=event.text)


class SendMediaDialog(ModalScreen[str | None]):
    """Ask whether a photo/video group is sent as pictures or as files.

    Dismisses with "media", "file", or None when cancelled.
    """

    DEFAULT_CSS = """
    SendMediaDialog {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }
    #send-media-box {
        width: 56;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #send-media-title {
        height: auto;
        text-style: bold;
        color: $accent;
    }
    #send-media-body {
        height: auto;
        margin-top: 1;
    }
    #send-media-hint {
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("p", "choose('media')", "Pictures", show=False),
        Binding("P", "choose('media')", "Pictures", show=False),
        Binding("f", "choose('file')", "Files", show=False),
        Binding("F", "choose('file')", "Files", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, paths: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.paths = list(paths)

    def compose(self) -> ComposeResult:
        names = [Path(path).name for path in self.paths[:8]]
        if len(self.paths) > 8:
            names.append(f"… and {len(self.paths) - 8} more")
        if len(self.paths) > 10:
            group = "More than 10 are split into albums of 10."
        elif len(self.paths) > 1:
            group = "They are sent together as one album."
        else:
            group = "Choose how this one is sent."
        with Vertical(id="send-media-box"):
            yield Static(f"Send {describe_media_group(self.paths)}", id="send-media-title")
            yield Static(
                group + "\n\n" + "\n".join(names) + "\n\nP  Send as pictures\nF  Send as files",
                id="send-media-body",
            )
            yield Static("Esc cancels", id="send-media-hint")

    def action_choose(self, choice: str) -> None:
        self.dismiss(choice)

    def action_cancel(self) -> None:
        self.dismiss(None)


def clipboard_text_for_widget(text: str, *, single_line: bool) -> str:
    """Normalize pasted text. Single-line fields keep the first line only."""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    if single_line:
        return cleaned.split("\n", 1)[0]
    if cleaned.endswith("\n"):
        cleaned = cleaned[:-1]
    return cleaned


def existing_file_paths(text: str) -> list[str]:
    """Return paths when the paste is nothing but existing files.

    Explorer quotes each path. A sentence that merely mentions a path is
    left as text.
    """
    raw = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    quoted = _QUOTED_PATH.findall(raw)
    if '"' in raw:
        if _QUOTED_PATH.sub("", raw).strip():
            return []
        if quoted and all(Path(path).is_file() for path in quoted):
            return quoted
        return []
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    if lines and all(Path(line).is_file() for line in lines):
        return lines
    parts = raw.split()
    if len(parts) > 1 and all(Path(part).is_file() for part in parts):
        return parts
    return []


def deliver_system_paste(app: Any, explicit_text: str | None = None) -> None:
    """Insert clipboard text, or hand file paths to the open chat."""
    payload = _payload(explicit_text)
    focused = getattr(app, "focused", None)
    screen = getattr(app, "screen", None)
    text_source = payload.text

    raw_files = list(payload.files)
    file_target = None
    if not isinstance(focused, Input) and screen is not None and hasattr(screen, "receive_file_paste"):
        file_target = screen
        if not raw_files:
            raw_files = existing_file_paths(text_source)

    if isinstance(focused, Input) and raw_files and _text_is_only_files(text_source, raw_files):
        return

    if file_target is not None and raw_files:
        file_target.receive_file_paste(raw_files)
        return

    target = focused if isinstance(focused, (Input, TextArea)) else None
    composer = getattr(screen, "composer", None)
    composer_area = getattr(composer, "text_area", None)
    if target is None and isinstance(composer_area, TextArea):
        target = composer_area
    if target is None:
        return

    cleaned = clipboard_text_for_widget(text_source, single_line=isinstance(target, Input))
    if cleaned == "":
        return
    if not claim_paste(app, "text\0" + cleaned):
        return
    _insert(target, cleaned)
    if (
        target is composer_area
        and screen is not None
        and hasattr(screen, "_set_focus_mode")
        and not getattr(screen, "is_write_mode", False)
    ):
        screen._set_focus_mode(True)


def _payload(explicit_text: str | None) -> ClipboardPayload:
    system = read_clipboard()
    # A bracketed paste carries the characters. Ctrl+V does not, so the
    # system clipboard is the text. File paths always come from CF_HDROP
    # when the copy was a group of files.
    if explicit_text is None:
        text = system.text
    else:
        text = explicit_text
    return ClipboardPayload(text=text or "", files=system.files)


def _text_is_only_files(text: str, files: list[str]) -> bool:
    if not text.strip():
        return True
    parsed = existing_file_paths(text)
    return bool(parsed) and set(parsed) == set(files)


def claim_paste(app: Any, token: str) -> bool:
    """Ignore the same paste arriving as both a key and a bracketed paste."""
    now = time.monotonic()
    last = getattr(app, "_paste_guard", None)
    if isinstance(last, tuple) and len(last) == 2 and last[0] == token and now - float(last[1]) < _PASTE_WINDOW_SECONDS:
        return False
    app._paste_guard = (token, now)
    return True


def _insert(widget: Input | TextArea, text: str) -> None:
    if isinstance(widget, Input):
        start, end = widget.selection
        widget.replace(text, start, end)
        return
    start, end = widget.selection
    if start > end:
        start, end = end, start
    result = widget.replace(text, start, end, maintain_selection_offset=False)
    widget.move_cursor(result.end_location)
