"""Main Textual application for tg-cli.
Manages application lifecycle, gateway injection, screen navigation, and Ctrl+C double-tap.
"""

from __future__ import annotations

import time
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.events import Key
from tg_cli.commands import Command
from tg_cli.domain.models import Account
from tg_cli.telegram.fake import FakeGateway
from tg_cli.telegram.gateway import Gateway
from tg_cli.ui.screens.accounts import AccountsScreen
from tg_cli.ui.screens.chats import ChatsScreen


class TelegramCLIApp(App[None]):
    """Telegram CLI Textual Application."""

    TITLE = "Telegram CLI"
    SUB_TITLE = "Keyboard-first client"

    BINDINGS = [
        Binding("ctrl+c", "handle_ctrl_c", "Cancel/Quit", show=False, priority=True),
    ]

    def __init__(
        self,
        gateway: Gateway | None = None,
        db_error: tuple[object, str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.gateway = gateway or FakeGateway()
        self.db_error = db_error
        self._last_ctrl_c_time: float = 0.0

    def on_mount(self) -> None:
        if self.db_error is not None:
            from tg_cli.ui.screens.db_error import DatabaseErrorScreen
            path, err = self.db_error
            self.push_screen(DatabaseErrorScreen(db_path=str(path), error_message=err))
            return

        accounts = self.gateway.get_accounts()
        if len(accounts) == 1:
            # Single account: open chat list directly, with accounts screen behind it
            self.push_screen(AccountsScreen(gateway=self.gateway))
            self.push_screen(ChatsScreen(gateway=self.gateway, account=accounts[0]))
        else:
            self.push_screen(AccountsScreen(gateway=self.gateway))

    def open_account(self, account: Account) -> None:
        """Navigate to chat list screen for the given account."""
        self.push_screen(ChatsScreen(gateway=self.gateway, account=account))

    def action_handle_ctrl_c(self) -> None:
        """Cancel in-flight action; second Ctrl+C within 1.0s quits."""
        now = time.time()
        if now - self._last_ctrl_c_time <= 1.0:
            self.exit()
        else:
            self._last_ctrl_c_time = now
            # Post notice to current screen if it has a status bar
            if hasattr(self.screen, "status_bar"):
                self.screen.status_bar.set_status("Press Ctrl+C again within 1s to quit.")
