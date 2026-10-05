"""Database error full-screen display."""

from __future__ import annotations

from pathlib import Path
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle, Vertical
from textual.screen import Screen
from textual.widgets import Static


class DatabaseErrorScreen(Screen):
    """Full-screen message when SQLite database cannot be opened or initialized."""

    DEFAULT_CSS = """
    DatabaseErrorScreen {
        align: center middle;
        background: $background;
    }
    #db-error-box {
        width: 76;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $error;
    }
    #db-error-title {
        color: $error;
        text-style: bold;
        margin-bottom: 1;
    }
    #db-error-content {
        color: $text;
        margin-bottom: 1;
    }
    #db-error-footer {
        color: $text-muted;
        text-style: italic;
    }
    """

    BINDINGS = [
        Binding("escape", "exit_app", "Exit", show=False),
        Binding("enter", "exit_app", "Exit", show=False),
        Binding("ctrl+c", "exit_app", "Exit", show=False),
    ]

    def __init__(self, db_path: Path | str, error_message: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.db_path = str(db_path)
        self.error_message = error_message

    def compose(self) -> ComposeResult:
        with Center():
            with Middle():
                with Vertical(id="db-error-box"):
                    yield Static("⚠ Database Initialization Failed", id="db-error-title")
                    content = (
                        f"Failed to open or initialize SQLite database at:\n"
                        f"  {self.db_path}\n\n"
                        f"Details:\n"
                        f"  {self.error_message}"
                    )
                    yield Static(content, id="db-error-content")
                    yield Static("Press Esc, Enter, or Ctrl+C to exit.", id="db-error-footer")

    def action_exit_app(self) -> None:
        self.app.exit()
