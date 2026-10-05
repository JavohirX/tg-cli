"""Accounts selection screen for tg-cli.
Allows selecting an account to view, adding new accounts, or removing sessions.
"""

from __future__ import annotations

from typing import Any
from rich.console import RenderableType
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Static
from tg_cli.domain.models import Account, AuthState
from tg_cli.telegram.gateway import Gateway
from tg_cli.ui.chrome import FooterBar, StatusBar
from tg_cli.ui.window import WindowedList


class AccountsScreen(Screen):
    """Account selection screen."""

    DEFAULT_CSS = """
    AccountsScreen {
        layout: vertical;
        background: $background;
    }
    #header-box {
        height: 3;
        padding: 1 2;
        background: $panel;
        color: $text;
        text-style: bold;
    }
    #list-container {
        height: 1fr;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("enter", "open_selected", "Open", show=False),
        Binding("a", "add_account", "Add", show=False),
        Binding("A", "add_account", "Add", show=False),
        Binding("d", "remove_account", "Remove", show=False),
        Binding("D", "remove_account", "Remove", show=False),
        Binding("q", "exit_app", "Exit", show=False),
        Binding("escape", "exit_app", "Exit", show=False),
        Binding("?", "show_help", "Help", show=False),
        Binding("ctrl+k", "show_palette", "Palette", show=False),
    ]

    def __init__(self, gateway: Gateway, **kwargs) -> None:
        super().__init__(**kwargs)
        self.gateway = gateway
        self.accounts_list = WindowedList[Account](
            items=[],
            renderer=self._render_account_row,
            id_fn=lambda acc: acc.user_id,
            id="accounts-window-list",
        )
        self.status_bar = StatusBar()
        self.footer_bar = FooterBar(context="accounts")

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Telegram CLI — Accounts", id="header-box")
            with Container(id="list-container"):
                yield self.accounts_list
            yield self.status_bar
            yield self.footer_bar

    def on_mount(self) -> None:
        self.refresh_accounts()
        self.accounts_list.focus()

    def refresh_accounts(self) -> None:
        accounts = self.gateway.get_accounts()
        self.accounts_list.set_items(accounts)
        if not accounts:
            self.status_bar.set_status("No accounts configured. Press 'A' to add an account.")
        else:
            self.status_bar.set_status(f"{len(accounts)} account(s) ready. Enter to open.")

    def _render_account_row(
        self, account: Account, width: int, is_cursor: bool, is_selected: bool
    ) -> RenderableType:
        line = Text()
        cursor_prefix = "> " if is_cursor else "  "
        line.append(cursor_prefix, style="bold cyan" if is_cursor else "default")

        label = account.label
        line.append(f"{label:<25} ", style="bold white" if is_cursor else "white")

        if account.display_name and account.display_name != label:
            line.append(f"({account.display_name}) ", style="dim")

        if account.auth_state == AuthState.EXPIRED:
            line.append("[Session Expired]", style="bold red")
        elif account.auth_state == AuthState.NEEDS_2FA:
            line.append("[Needs 2FA]", style="yellow")
        else:
            line.append("[Active]", style="green")

        if is_cursor:
            line.stylize("on grey15")
        return line

    def on_windowed_list_item_activated(self, event: WindowedList.ItemActivated) -> None:
        account: Account = event.item
        self.app.open_account(account)

    def action_open_selected(self) -> None:
        item = self.accounts_list.get_focused_item()
        if item is not None:
            self.app.open_account(item)

    def action_add_account(self) -> None:
        self.status_bar.set_status("Account addition is enabled in Phase 1.")

    def action_remove_account(self) -> None:
        item = self.accounts_list.get_focused_item()
        if item is not None:
            self.status_bar.set_status(f"Removal of {item.label} requested (destructive action).")

    def action_exit_app(self) -> None:
        self.app.exit()

    def action_show_help(self) -> None:
        from tg_cli.ui.screens.help import HelpScreen
        self.app.push_screen(HelpScreen(context="accounts"))

    def action_show_palette(self) -> None:
        from tg_cli.ui.screens.palette import CommandPalette
        self.app.push_screen(CommandPalette(context="accounts"))
