"""Active Sessions Manager screen for tg-cli.
Inspects active Telegram authorizations across devices, platforms, and locations.
Provides one-key session revocation with confirmation.
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
from tg_cli.domain.models import Account, SessionInfo
from tg_cli.telegram.gateway import Gateway
from tg_cli.ui.chrome import FooterBar, StatusBar
from tg_cli.ui.window import WindowedList


class SessionsScreen(Screen):
    """Screen for inspecting and revoking active Telegram authorizations."""

    DEFAULT_CSS = """
    SessionsScreen {
        layout: vertical;
        background: $background;
    }
    #sessions-header {
        height: 3;
        padding: 1 2;
        background: $panel;
        color: $text;
        text-style: bold;
    }
    #sessions-container {
        height: 1fr;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("d", "revoke_selected", "Revoke", show=False),
        Binding("D", "revoke_selected", "Revoke", show=False),
        Binding("r", "refresh_sessions", "Refresh", show=False),
        Binding("R", "refresh_sessions", "Refresh", show=False),
        Binding("escape", "go_back", "Back", show=False),
        Binding("?", "show_help", "Help", show=False),
        Binding("ctrl+k", "show_palette", "Palette", show=False),
    ]

    def __init__(self, gateway: Gateway, account: Account, **kwargs) -> None:
        super().__init__(**kwargs)
        self.gateway = gateway
        self.account = account
        self._pending_confirm: tuple[str, Callable[[], None]] | None = None

        self.sessions_list = WindowedList[SessionInfo](
            items=[],
            renderer=self._render_session_row,
            id_fn=lambda s: s.hash,
            id="sessions-window-list",
        )
        self.header_widget = Static(
            f"Telegram Sessions — {self.account.label}", id="sessions-header"
        )
        self.status_bar = StatusBar()
        self.footer_bar = FooterBar(context="sessions")

    def compose(self) -> ComposeResult:
        with Vertical():
            yield self.header_widget
            with Container(id="sessions-container"):
                yield self.sessions_list
            yield self.status_bar
            yield self.footer_bar

    def on_mount(self) -> None:
        self.action_refresh_sessions()
        self.sessions_list.focus()

    def action_refresh_sessions(self) -> None:
        if hasattr(self.gateway, "get_active_sessions"):
            sessions = self.gateway.get_active_sessions(self.account.user_id)
        else:
            sessions = []
        self.sessions_list.set_items(sessions)
        count = len(sessions)
        self.status_bar.set_status(
            f"{count} active session(s) found. Press 'd' to revoke a session."
        )

    def _render_session_row(
        self, session: SessionInfo, width: int, is_cursor: bool, is_selected: bool
    ) -> RenderableType:
        line = Text()
        cursor_prefix = "> " if is_cursor else "  "
        line.append(cursor_prefix, style="bold cyan" if is_cursor else "default")

        # Device & platform
        title = session.title
        line.append(f"{title:<26} ", style="bold white" if is_cursor else "white")

        # Location & IP
        loc_str = f"{session.country} ({session.ip})" if (session.country or session.ip) else ""
        line.append(f"{loc_str:<28} ", style="dim")

        # Last active date
        if session.date_active:
            dt_str = session.date_active.strftime("%Y-%m-%d %H:%M")
        else:
            dt_str = "recently"
        line.append(f"{dt_str:<18} ", style="dim italic")

        # Current session badge
        if session.is_current:
            line.append("[Current Session]", style="bold green")
        else:
            line.append("[Active]", style="cyan")

        if is_cursor:
            line.stylize("on grey15")
        return line

    def action_revoke_selected(self) -> None:
        item = self.sessions_list.get_focused_item()
        if item is None:
            return

        if item.is_current:
            self.status_bar.set_status("Cannot revoke current active session.", is_error=True)
            return

        def do_revoke() -> None:
            msg = f"Revoked session: {item.title}."
            is_err = False
            if hasattr(self.gateway, "revoke_session"):
                success = self.gateway.revoke_session(self.account.user_id, item.hash)
                if not success:
                    msg = f"Failed to revoke session: {item.title}."
                    is_err = True
            self.action_refresh_sessions()
            self.status_bar.set_status(msg, is_error=is_err)

        self._request_confirm(f"Revoke session on '{item.title}' ({item.ip})? (y/n)", do_revoke)

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
                self.status_bar.set_status("Revocation cancelled.")
                event.prevent_default()
                event.stop()
                return

    def action_go_back(self) -> None:
        if self._pending_confirm is not None:
            self._pending_confirm = None
            self.status_bar.set_prompt(None)
            return
        self.app.pop_screen()

    def action_show_help(self) -> None:
        from tg_cli.ui.screens.help import HelpScreen
        self.app.push_screen(HelpScreen(context="sessions"))

    def action_show_palette(self) -> None:
        from tg_cli.ui.screens.palette import CommandPalette
        self.app.push_screen(
            CommandPalette(context="global"),
            callback=self._handle_palette_command,
        )

    def _handle_palette_command(self, cmd: Any) -> None:
        if cmd is None:
            return
        match cmd.id:
            case "help":
                self.action_show_help()
            case "back":
                self.action_go_back()
