"""Telegram login screen.
Handles API credentials setup, phone input, SMS/Telegram code, 2FA password,
and Terminal ASCII QR Code Login with live countdown progress.
"""

from __future__ import annotations

import asyncio
from typing import Any
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Input, Static
from tg_cli.config import has_api_credentials, load_config, save_config
from tg_cli.ui.qr import generate_qr_ascii, render_progress_bar


class LoginScreen(ModalScreen[bool]):
    """Modal screen for Telegram authentication flow."""

    DEFAULT_CSS = """
    LoginScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.8);
    }
    #login-box {
        width: 76;
        height: auto;
        max-height: 95vh;
        overflow-y: auto;
        min-height: 16;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #login-title {
        height: 2;
        text-align: center;
        text-style: bold;
        color: $accent;
    }
    #login-hint {
        height: auto;
        color: $text-muted;
        margin-bottom: 1;
        text-align: center;
    }
    #login-qr {
        width: 100%;
        height: auto;
        content-align: center middle;
        margin-bottom: 1;
        display: none;
    }
    #login-progress {
        width: 100%;
        text-align: center;
        color: $accent;
        margin-bottom: 1;
        display: none;
    }
    #login-input {
        width: 100%;
        margin-bottom: 1;
        /* Default Input border is `tall` (eighth-blocks ▊▔▎▁). Console fonts
           that lack those glyphs draw a replacement "?", so the phone field
           looks like a box of question marks. Box-drawing `solid` is in those fonts. */
        border: solid $primary;
    }
    #login-input:focus {
        border: solid $accent;
    }
    #login-error {
        height: auto;
        color: $error;
        text-style: bold;
        margin-bottom: 1;
        text-align: center;
    }
    #login-status {
        height: 1;
        color: $text-muted;
        text-align: center;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel_login", "Cancel", show=False),
        Binding("ctrl+q", "toggle_qr_mode", "Toggle QR", show=False),
        Binding("p", "switch_to_phone", "Phone", show=False),
        Binding("P", "switch_to_phone", "Phone", show=False),
        Binding("r", "refresh_qr", "Refresh", show=False),
        Binding("R", "refresh_qr", "Refresh", show=False),
    ]

    def __init__(self, gateway: Any, start_mode: str = "phone", **kwargs) -> None:
        super().__init__(**kwargs)
        self.gateway = gateway
        self.config = load_config()

        # Steps: 'credentials', 'phone', 'qr', 'code', 'password'
        self.step: str = start_mode
        if not self.config.get("api_id") or not self.config.get("api_hash"):
            self.step = "credentials"

        self.api_id: int = self.config.get("api_id") or 0
        self.api_hash: str = self.config.get("api_hash") or ""
        self.phone: str = ""
        self.error_message: str = ""

        # QR state
        self.qr_url: str = ""
        self.qr_remaining_seconds: int = 30
        self.qr_total_seconds: int = 30
        self._qr_timer = None

        self.title_widget = Static("Add Telegram Account", id="login-title")
        self.hint_widget = Static("", id="login-hint")
        self.qr_widget = Static("", id="login-qr")
        self.qr_widget.can_focus = True
        self.progress_widget = Static("", id="login-progress")
        self.input_widget = Input(id="login-input")
        self.error_widget = Static("", id="login-error")
        self.status_widget = Static("Press Esc to cancel | Ctrl+Q for QR Login", id="login-status")

    def compose(self) -> ComposeResult:
        with Vertical(id="login-box"):
            yield self.title_widget
            yield self.hint_widget
            yield self.qr_widget
            yield self.progress_widget
            yield self.input_widget
            yield self.error_widget
            yield self.status_widget

    def on_mount(self) -> None:
        if hasattr(self.gateway, "register_listener"):
            self.gateway.register_listener(self._on_gateway_event)
        self._update_step_ui()

    def on_unmount(self) -> None:
        if hasattr(self.gateway, "unregister_listener"):
            self.gateway.unregister_listener(self._on_gateway_event)
        self._stop_qr_timer()

    def _on_gateway_event(self, event_type: str, data: dict[str, Any]) -> None:
        def _dispatch() -> None:
            if event_type == "qr_login_token":
                self.on_qr_token(data.get("url", ""), data.get("expires_in", 30))
            elif event_type == "login_code_sent":
                self.on_login_code_sent(data.get("phone", ""))
            elif event_type == "login_2fa_needed":
                self.on_login_2fa_needed(data.get("hint", ""))
            elif event_type == "login_error":
                self.on_login_error(data.get("error", ""))
            elif event_type == "login_success":
                self.on_login_success()

        if self.is_mounted and hasattr(self, "app") and self.app:
            try:
                self.app.call_from_thread(_dispatch)
                return
            except Exception:
                pass
        _dispatch()

    def _stop_qr_timer(self) -> None:
        if self._qr_timer is not None:
            self._qr_timer.stop()
            self._qr_timer = None

    def _update_step_ui(self) -> None:
        self.error_message = ""
        self.error_widget.update("")
        self.input_widget.value = ""

        if self.step == "credentials":
            self.qr_widget.display = False
            self.progress_widget.display = False
            self.input_widget.display = True
            self.title_widget.update("Step 1: API ID & Hash")
            self.hint_widget.update(
                "Enter your api_id and api_hash from my.telegram.org\n"
                "Format: <api_id>:<api_hash> (e.g. 123456:abcdef123456)"
            )
            self.input_widget.placeholder = "123456:abcdef123456"
            self.input_widget.password = False
            self.status_widget.update("Press Esc to cancel")
            self.input_widget.focus()

        elif self.step == "qr":
            self.input_widget.display = False
            self.qr_widget.display = True
            self.progress_widget.display = True
            self.title_widget.update("Terminal QR Code Login")
            self.hint_widget.update(
                "Scan with Telegram: Settings -> Devices -> Link Desktop Device\n"
                "Press 'P' or Ctrl+Q for phone login | Esc to cancel"
            )
            self.status_widget.update("Waiting for scan from Telegram mobile app...")
            self.qr_widget.focus()
            self._request_qr_token()

        elif self.step == "phone":
            self._stop_qr_timer()
            self.qr_widget.display = False
            self.progress_widget.display = False
            self.input_widget.display = True
            self.title_widget.update("Step 2: Phone Number")
            self.hint_widget.update(
                "Enter international phone number (e.g. +12025550101)\n"
                "Press Ctrl+Q to switch to QR Login mode"
            )
            self.input_widget.placeholder = "+12025550101"
            self.input_widget.password = False
            self.status_widget.update("Press Enter to request code | Ctrl+Q for QR | Esc to cancel")
            self.input_widget.focus()

        elif self.step == "code":
            self._stop_qr_timer()
            self.qr_widget.display = False
            self.progress_widget.display = False
            self.input_widget.display = True
            self.title_widget.update("Step 3: Verification Code")
            self.hint_widget.update(f"Code sent to {self.phone} via Telegram/SMS:")
            self.input_widget.placeholder = "12345"
            self.input_widget.password = False
            self.status_widget.update("Enter verification code | Esc to cancel")
            self.input_widget.focus()

        elif self.step == "password":
            self._stop_qr_timer()
            self.qr_widget.display = False
            self.progress_widget.display = False
            self.input_widget.display = True
            self.title_widget.update("Step 4: Two-Step Verification (2FA)")
            self.hint_widget.update("Enter your 2FA password:")
            self.input_widget.placeholder = "Password"
            self.input_widget.password = True
            self.status_widget.update("Enter 2FA password | Esc to cancel")
            self.input_widget.focus()

    def _request_qr_token(self) -> None:
        self.progress_widget.update("Generating QR Code token...")
        if hasattr(self.gateway, "start_qr_login"):
            self.gateway.start_qr_login(self.api_id, self.api_hash)
        else:
            # Fallback mock for testing
            mock_url = "tg://login?token=mock_test_token"
            self.on_qr_token(mock_url, 30)

    def on_qr_token(self, url: str, expires_in: int = 30) -> None:
        self.qr_url = url
        self.qr_remaining_seconds = expires_in
        self.qr_total_seconds = expires_in

        # Generate half-block QR lines
        lines = generate_qr_ascii(url, border=1, invert=True)
        rendered_qr = "\n".join(lines)
        self.qr_widget.update(Text(rendered_qr, justify="center"))

        self.progress_widget.update(
            render_progress_bar(self.qr_remaining_seconds, self.qr_total_seconds)
        )

        self._stop_qr_timer()
        self._qr_timer = self.set_interval(1.0, self._tick_qr_timer)

    def _tick_qr_timer(self) -> None:
        if self.step != "qr":
            self._stop_qr_timer()
            return

        self.qr_remaining_seconds -= 1
        if self.qr_remaining_seconds <= 0:
            self.progress_widget.update("Refreshing QR token...")
            self._stop_qr_timer()
            self._request_qr_token()
        else:
            self.progress_widget.update(
                render_progress_bar(self.qr_remaining_seconds, self.qr_total_seconds)
            )

    def action_toggle_qr_mode(self) -> None:
        if self.step == "qr":
            self.step = "phone"
        else:
            self.step = "qr"
        self._update_step_ui()

    def action_switch_to_phone(self) -> None:
        if self.step == "qr":
            self.step = "phone"
            self._update_step_ui()

    def action_refresh_qr(self) -> None:
        if self.step == "qr":
            self._request_qr_token()

    def on_key(self, event: Key) -> None:
        if event.key == "ctrl+q":
            self.action_toggle_qr_mode()
            event.prevent_default()
            event.stop()
            return

        if self.step == "qr":
            key_char = (event.character or event.key or "").lower()
            if key_char == "p":
                self.action_switch_to_phone()
                event.prevent_default()
                event.stop()
            elif key_char == "r":
                self.action_refresh_qr()
                event.prevent_default()
                event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        if not val:
            return

        if self.step == "credentials":
            if ":" not in val:
                self.error_widget.update("Expected format: <api_id>:<api_hash>")
                return
            parts = val.split(":", 1)
            try:
                self.api_id = int(parts[0].strip())
                self.api_hash = parts[1].strip()
            except ValueError:
                self.error_widget.update("api_id must be a number")
                return

            save_config(self.api_id, self.api_hash)
            self.step = "phone"
            self._update_step_ui()

        elif self.step == "phone":
            self.phone = val
            self.error_widget.update("")
            self.status_widget.update("Requesting verification code...")

            if hasattr(self.gateway, "start_login"):
                self.gateway.start_login(
                    phone=self.phone,
                    api_id=self.api_id,
                    api_hash=self.api_hash,
                )
            else:
                self.on_login_code_sent(self.phone)

        elif self.step == "code":
            self.error_widget.update("")
            self.status_widget.update("Verifying code...")
            if hasattr(self.gateway, "submit_login_code"):
                self.gateway.submit_login_code(val)
            else:
                self.on_login_success()

        elif self.step == "password":
            self.error_widget.update("")
            self.status_widget.update("Verifying 2FA password...")
            if hasattr(self.gateway, "submit_login_password"):
                self.gateway.submit_login_password(val)
            else:
                self.on_login_success()

    def on_login_code_sent(self, phone: str) -> None:
        self.step = "code"
        self._update_step_ui()
        self.status_widget.update("Code received? Enter it above.")

    def on_login_2fa_needed(self, hint: str) -> None:
        self.step = "password"
        self._update_step_ui()
        if hint:
            self.hint_widget.update(f"2FA Active: {hint}")

    def on_login_error(self, error: str) -> None:
        self.error_message = error
        self.error_widget.update(f"[!] {error}")
        self.status_widget.update("Please check and try again.")
        if self.step != "qr":
            self.input_widget.focus()

    def on_login_success(self) -> None:
        self._stop_qr_timer()
        self.dismiss(True)

    def action_cancel_login(self) -> None:
        self._stop_qr_timer()
        if hasattr(self.gateway, "cancel_qr_login") and self.step == "qr":
            self.gateway.cancel_qr_login()
        elif hasattr(self.gateway, "cancel_login"):
            self.gateway.cancel_login()
        self.dismiss(False)
