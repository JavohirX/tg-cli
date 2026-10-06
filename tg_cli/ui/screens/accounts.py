"""Accounts selection screen for tg-cli.
Allows selecting an account to view, adding new accounts, or removing sessions.
"""

from __future__ import annotations

from typing import Any, Callable
from rich.console import RenderableType
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.events import Key
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
        Binding("q", "qr_login", "QR Login", show=False),
        Binding("Q", "qr_login", "QR Login", show=False),
        Binding("s", "manage_sessions", "Sessions", show=False),
        Binding("S", "manage_sessions", "Sessions", show=False),
        Binding("p", "manage_profile", "Profile", show=False),
        Binding("P", "manage_profile", "Profile", show=False),
        Binding("d", "remove_account", "Remove", show=False),
        Binding("D", "remove_account", "Remove", show=False),
        Binding("escape", "exit_app", "Exit", show=False),
        Binding("?", "show_help", "Help", show=False),
        Binding("ctrl+k", "show_palette", "Palette", show=False),
    ]

    def __init__(self, gateway: Gateway, **kwargs) -> None:
        super().__init__(**kwargs)
        self.gateway = gateway
        self.unread_counts: dict[int, int] = {}
        self._pending_confirm: tuple[str, Callable[[], None]] | None = None
        self.accounts_list = WindowedList[Account](
            items=[],
            renderer=self._render_account_row,
            id_fn=lambda acc: acc.user_id,
            id="accounts-window-list",
            enable_digit_motion=False,
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
        if hasattr(self.gateway, "get_account_unread_counts"):
            try:
                self.unread_counts = self.gateway.get_account_unread_counts()
            except Exception:
                self.unread_counts = {}
        self.accounts_list.set_items(accounts)

        from tg_cli.config import get_proxy_config
        proxy_cfg = get_proxy_config()
        proxy_info = f" · [proxy: {proxy_cfg.get('type')}]" if proxy_cfg.get("enabled") else ""

        if not accounts:
            self.status_bar.set_status(f"No accounts configured. Press 'A' to add, 'Q' for QR login.{proxy_info}")
        else:
            self.status_bar.set_status(f"{len(accounts)} account(s) ready. Enter/1-9 to open · Q for QR · S for sessions.{proxy_info}")

    def _render_account_row(
        self, account: Account, width: int, is_cursor: bool, is_selected: bool
    ) -> RenderableType:
        line = Text()
        cursor_prefix = "> " if is_cursor else "  "
        line.append(cursor_prefix, style="bold cyan" if is_cursor else "default")

        # Number prefix (1-9)
        idx = next((i for i, a in enumerate(self.accounts_list.items) if a.user_id == account.user_id), 0)
        num_prefix = f"{idx + 1}: " if idx < 9 else ""

        # Unread badge
        unread = self.unread_counts.get(account.user_id, 0)
        unread_badge = f" ({unread})" if unread > 0 else ""

        pill = f"[{num_prefix}{account.label}{unread_badge}]"
        if unread > 0:
            line.append(f"{pill:<28} ", style="bold yellow" if not is_cursor else "bold white")
        else:
            line.append(f"{pill:<28} ", style="bold white" if is_cursor else "white")

        if account.display_name and account.display_name != account.label:
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
        self._open_account(account)

    def _open_account(self, account: Account) -> None:
        if account.auth_state == AuthState.EXPIRED:
            self.status_bar.set_status(f"Session for {account.label} expired. Please re-authenticate.", is_error=True)
            self.action_add_account()
            return
        self.app.open_account(account)

    def action_open_selected(self) -> None:
        item = self.accounts_list.get_focused_item()
        if item is not None:
            self._open_account(item)

    def action_qr_login(self) -> None:
        from tg_cli.ui.screens.login import LoginScreen

        def on_login_done(success: bool) -> None:
            if success:
                self.refresh_accounts()
                self.status_bar.set_status("Account authenticated via QR code successfully.")
            self.accounts_list.focus()

        self.app.push_screen(LoginScreen(gateway=self.gateway, start_mode="qr"), on_login_done)

    def action_manage_sessions(self) -> None:
        item = self.accounts_list.get_focused_item()
        if item is None:
            return
        from tg_cli.ui.screens.sessions import SessionsScreen
        self.app.push_screen(SessionsScreen(gateway=self.gateway, account=item))

    def action_manage_profile(self) -> None:
        item = self.accounts_list.get_focused_item()
        if item is None:
            return
        from tg_cli.ui.screens.profile import ProfileScreen
        self.app.push_screen(ProfileScreen(gateway=self.gateway, account=item))

    def action_toggle_proxy(self) -> None:
        from tg_cli.config import get_proxy_config, probe_proxy_latency, toggle_proxy
        new_state = toggle_proxy()
        if new_state:
            cfg = get_proxy_config()
            lat = probe_proxy_latency(cfg)
            lat_str = f" ({lat}ms)" if lat is not None else ""
            self.status_bar.set_toast(f"Proxy enabled: {cfg.get('type')} {cfg.get('addr')}:{cfg.get('port')}{lat_str}")
        else:
            self.status_bar.set_toast("Proxy disabled.")
        self.refresh_accounts()

    def action_add_account(self) -> None:
        from tg_cli.ui.screens.login import LoginScreen

        def on_login_done(success: bool) -> None:
            if success:
                self.refresh_accounts()
                self.status_bar.set_status("Account authenticated successfully.")
            self.accounts_list.focus()

        self.app.push_screen(LoginScreen(gateway=self.gateway), on_login_done)

    def action_remove_account(self) -> None:
        item = self.accounts_list.get_focused_item()
        if item is None:
            return

        def do_remove() -> None:
            if hasattr(self.gateway, "remove_account"):
                self.gateway.remove_account(item.user_id)
            self.refresh_accounts()
            self.status_bar.set_status(f"Removed account {item.label}.")

        self._request_confirm(f"Remove account {item.label}? (y/n)", do_remove)

    def _request_confirm(self, prompt: str, on_confirm: Callable[[], None]) -> None:
        self._pending_confirm = (prompt, on_confirm)
        self.status_bar.set_prompt(prompt)

    def on_key(self, event: Key) -> None:
        if self._pending_confirm is not None:
            prompt, on_confirm = self._pending_confirm
            if event.character in ("y", "Y"):
                self._pending_confirm = None
                self.status_bar.set_prompt(None)
                on_confirm()
                event.prevent_default()
                event.stop()
                return
            elif event.character in ("n", "N") or event.key == "escape":
                self._pending_confirm = None
                self.status_bar.set_prompt(None)
                self.status_bar.set_status("Cancelled.")
                event.prevent_default()
                event.stop()
                return

        # Instant account switcher via 1-9
        key_char = event.character or event.key
        if key_char and key_char in "123456789":
            idx = int(key_char) - 1
            if 0 <= idx < len(self.accounts_list.items):
                target_account = self.accounts_list.items[idx]
                self._open_account(target_account)
                event.prevent_default()
                event.stop()
                return

    def action_exit_app(self) -> None:
        if self._pending_confirm is not None:
            self._pending_confirm = None
            self.status_bar.set_prompt(None)
            return
        self.app.exit()

    def action_show_help(self) -> None:
        from tg_cli.ui.screens.help import HelpScreen
        self.app.push_screen(HelpScreen(context="accounts"))

    def action_show_palette(self) -> None:
        from tg_cli.ui.screens.palette import CommandPalette
        self.app.push_screen(
            CommandPalette(context="accounts"),
            callback=self._handle_palette_command,
        )

    def _handle_palette_command(self, cmd: Any) -> None:
        if cmd is None:
            return

        def execute() -> None:
            match cmd.id:
                case "add_account":
                    self.action_add_account()
                case "qr_login":
                    self.action_qr_login()
                case "toggle_proxy":
                    self.action_toggle_proxy()
                case "manage_sessions":
                    self.action_manage_sessions()
                case "manage_profile":
                    self.action_manage_profile()
                case "remove_account":
                    self.action_remove_account()
                case "open_account":
                    self.action_open_selected()
                case "help":
                    self.action_show_help()

        if getattr(cmd, "destructive", False):
            self._request_confirm(
                f"Run '{cmd.title}'? (y/n)",
                execute,
            )
        else:
            execute()
