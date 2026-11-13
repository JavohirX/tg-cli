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
from tg_cli.domain.render import apply_terminal_cell_width
from tg_cli.ui.clipboard import copy_text
from tg_cli.ui.screens.accounts import AccountsScreen
from tg_cli.ui.screens.chats import ChatsScreen

# Screen imports pull Textual in before the width patch runs inside render.
apply_terminal_cell_width()

APP_NAME = "tg-cli"

# OSC 0 sets the tab title in Windows Terminal and most other terminals.
# An empty title hands the tab back to the shell.
RESET_TERMINAL_TITLE = "\x1b]0;\x07"


def terminal_title(unread: int) -> str:
    """Tab title with the unread count up front, as browsers do."""
    return f"({unread}) {APP_NAME}" if unread > 0 else APP_NAME


class TelegramCLIApp(App[None]):
    """Telegram CLI Textual Application."""

    TITLE = "Telegram CLI"
    SUB_TITLE = "Keyboard-first client"

    BINDINGS = [
        Binding("ctrl+c", "handle_ctrl_c", "Cancel/Quit", show=False, priority=True),
        Binding("ctrl+q", "toggle_qr_mode", "Toggle QR", show=False, priority=True),
        Binding("ctrl+v,shift+insert,ctrl+shift+v", "paste_clipboard", "Paste", show=False),
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
        self._terminal_title: str | None = None

    def on_mount(self) -> None:
        # Polling also catches local changes such as `r`, which emit no event.
        self.set_interval(2.0, self.update_terminal_title)
        self.update_terminal_title()
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

    def update_terminal_title(self) -> None:
        """Unread across all accounts. Muted and archived chats do not count."""
        try:
            counts = self.gateway.get_account_unread_counts()
        except Exception:
            return
        title = terminal_title(sum(counts.values()))
        if title != self._terminal_title:
            self._terminal_title = title
            self._write_terminal_title(title)

    def _write_terminal_title(self, title: str) -> None:
        driver = getattr(self, "_driver", None)
        if driver is None:
            return
        try:
            driver.write(f"\x1b]0;{title}\x07")
        except Exception:
            pass

    def open_account(self, account: Account) -> None:
        """Navigate to chat list screen for the given account."""
        self.push_screen(ChatsScreen(gateway=self.gateway, account=account))

    def action_paste_clipboard(self) -> None:
        """Paste system-clipboard text when the focused widget has no paste action."""
        from tg_cli.ui.paste import deliver_system_paste
        deliver_system_paste(self)

    def action_toggle_qr_mode(self) -> None:
        if hasattr(self.screen, "action_toggle_qr_mode"):
            self.screen.action_toggle_qr_mode()

    def action_handle_ctrl_c(self) -> None:
        """Copy a text selection. With nothing selected, second Ctrl+C within 1s quits."""
        selected = ""
        try:
            selected = self.screen.get_selected_text() or ""
        except Exception:
            selected = ""
        if selected.strip():
            if copy_text(self, selected):
                status = getattr(self.screen, "status_bar", None)
                if status is not None and hasattr(status, "set_status"):
                    status.set_status("Copied.")
            return
        now = time.time()
        if now - self._last_ctrl_c_time <= 1.0:
            self.exit()
        else:
            self._last_ctrl_c_time = now
            # Post notice to current screen if it has a status bar
            if hasattr(self.screen, "status_bar"):
                self.screen.status_bar.set_status("Press Ctrl+C again within 1s to quit.")
