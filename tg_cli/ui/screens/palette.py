"""Command palette modal.
Opens with Ctrl+K, filters commands from commands.py, and executes actions.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option
from tg_cli.commands import Command, get_commands_for_context


class CommandPalette(ModalScreen[Command | None]):
    """Command palette dialog for discovering and triggering actions."""

    DEFAULT_CSS = """
    CommandPalette {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }
    #palette-container {
        width: 60;
        height: 18;
        background: $surface;
        border: thick $primary;
        padding: 1;
    }
    #palette-input {
        width: 100%;
        margin-bottom: 1;
    }
    #palette-options {
        height: 1fr;
        border: none;
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_palette", "Close", show=False),
        Binding("enter", "select_option", "Execute", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
    ]

    def __init__(self, context: str = "chats", **kwargs) -> None:
        super().__init__(**kwargs)
        self.context = context
        self.available_commands: list[Command] = get_commands_for_context(context)
        self.filtered_commands: list[Command] = list(self.available_commands)

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-container"):
            yield Input(placeholder="Type a command...", id="palette-input")
            yield OptionList(id="palette-options")

    def on_mount(self) -> None:
        self._populate_options()
        self.query_one(Input).focus()

    def _populate_options(self) -> None:
        option_list = self.query_one(OptionList)
        option_list.clear_options()

        for cmd in self.filtered_commands:
            prompt = Text()
            prompt.append(f"{cmd.title:<30} ", style="bold")
            prompt.append(f"[{cmd.display_key}]", style="cyan")
            if cmd.destructive:
                prompt.append(" ⚠", style="bold red")
            option_list.add_option(Option(prompt, id=cmd.id))

        if self.filtered_commands:
            option_list.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        if not query:
            self.filtered_commands = list(self.available_commands)
        else:
            self.filtered_commands = [
                cmd
                for cmd in self.available_commands
                if query in cmd.title.lower()
                or query in cmd.id.lower()
                or query in cmd.description.lower()
            ]
        self._populate_options()

    def on_key(self, event: Key) -> None:
        if event.key in ("down", "ctrl+n"):
            option_list = self.query_one(OptionList)
            if option_list.highlighted is not None:
                option_list.highlighted = min(len(self.filtered_commands) - 1, option_list.highlighted + 1)
            event.prevent_default()
            event.stop()
        elif event.key in ("up", "ctrl+p"):
            option_list = self.query_one(OptionList)
            if option_list.highlighted is not None:
                option_list.highlighted = max(0, option_list.highlighted - 1)
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            self.action_select_option()
            event.prevent_default()
            event.stop()

    def action_dismiss_palette(self) -> None:
        self.dismiss(None)

    def action_select_option(self) -> None:
        option_list = self.query_one(OptionList)
        if option_list.highlighted is not None and 0 <= option_list.highlighted < len(self.filtered_commands):
            selected_cmd = self.filtered_commands[option_list.highlighted]
            self.dismiss(selected_cmd)
        else:
            self.dismiss(None)
