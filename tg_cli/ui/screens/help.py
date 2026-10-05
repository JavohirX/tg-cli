"""Help reference screen displaying shortcuts from commands.py."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static
from tg_cli.commands import get_commands_for_context


class HelpScreen(ModalScreen[None]):
    """Modal screen displaying keyboard shortcuts for current context."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }
    #help-container {
        width: 76;
        height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #help-scroll {
        height: 1fr;
    }
    #help-footer {
        height: 1;
        text-align: center;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_help", "Close", show=False),
        Binding("?", "dismiss_help", "Close", show=False),
        Binding("q", "dismiss_help", "Close", show=False),
        Binding("j", "scroll_down", "Down", show=False),
        Binding("k", "scroll_up", "Up", show=False),
    ]

    def __init__(self, context: str = "chats", **kwargs) -> None:
        super().__init__(**kwargs)
        self.context = context

    def compose(self) -> ComposeResult:
        with Container(id="help-container"):
            with VerticalScroll(id="help-scroll"):
                yield Static(self._build_help_table())
            yield Static("Press Esc or ? to return", id="help-footer")

    def on_mount(self) -> None:
        from tg_cli.config import mark_help_seen
        try:
            mark_help_seen()
        except Exception:
            pass

    def _build_help_table(self) -> Table:
        table = Table(
            title=f"Keyboard Shortcuts ({self.context.title()} View)",
            expand=True,
            show_header=True,
            header_style="bold cyan",
            box=None,
        )
        table.add_column("Key", style="bold yellow", width=16)
        table.add_column("Action", style="bold white", width=24)
        table.add_column("Description", style="dim", width=32)

        commands = get_commands_for_context(self.context)
        for cmd in commands:
            desc = cmd.description
            if cmd.undoable:
                desc += " (undoable 5s)"
            if cmd.destructive:
                desc += " ⚠"
            table.add_row(cmd.display_key, cmd.title, desc)

        return table

    def action_dismiss_help(self) -> None:
        self.dismiss(None)

    def action_scroll_down(self) -> None:
        self.query_one(VerticalScroll).scroll_relative(y=1)

    def action_scroll_up(self) -> None:
        self.query_one(VerticalScroll).scroll_relative(y=-1)
