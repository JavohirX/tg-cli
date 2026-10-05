"""Telegram login screen.
Handles API credentials setup, phone input, SMS/Telegram code, and 2FA password.
Errors are shown directly underneath input fields.
"""

from __future__ import annotations

from typing import Any
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static
from tg_cli.config import has_api_credentials, load_config, save_config


class LoginScreen(ModalScreen[bool]):
    """Modal screen for Telegram authentication flow."""

    DEFAULT_CSS = """
    LoginScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.8);
    }
    #login-box {
        width: 64;
        height: auto;
        min-height: 14;
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
    }
    #login-input {
        width: 100%;
        margin-bottom: 1;
    }
    #login-error {
        height: auto;
        color: $error;
        text-style: bold;
        margin-bottom: 1;
    }
    #login-status {
        height: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel_login", "Cancel", show=False),
    ]

    def __init__(self, gateway: Any, **kwargs) -> None:
        super().__init__(**kwargs)
        self.gateway = gateway
        self.config = load_config()

        # Steps: 'credentials', 'phone', 'code', 'password'
        self.step: str = "phone"
        if not self.config.get("api_id") or not self.config.get("api_hash"):
            self.step = "credentials"

        self.api_id: int = self.config.get("api_id") or 0
        self.api_hash: str = self.config.get("api_hash") or ""
        self.phone: str = ""
        self.error_message: str = ""

        self.title_widget = Static("Add Telegram Account", id="login-title")
        self.hint_widget = Static("", id="login-hint")
        self.input_widget = Input(id="login-input")
        self.error_widget = Static("", id="login-error")
        self.status_widget = Static("Press Esc to cancel", id="login-status")

    def compose(self) -> ComposeResult:
        with Vertical(id="login-box"):
            yield self.title_widget
            yield self.hint_widget
            yield self.input_widget
            yield self.error_widget
            yield self.status_widget

    def on_mount(self) -> None:
        self._update_step_ui()

    def _update_step_ui(self) -> None:
        self.error_message = ""
        self.error_widget.update("")
        self.input_widget.value = ""

        if self.step == "credentials":
            self.title_widget.update("Step 1: API ID & Hash")
            self.hint_widget.update(
                "Enter your api_id and api_hash from my.telegram.org\n"
                "Format: <api_id>:<api_hash> (e.g. 123456:abcdef123456)"
            )
            self.input_widget.placeholder = "123456:abcdef123456"
            self.input_widget.password = False

        elif self.step == "phone":
            self.title_widget.update("Step 2: Phone Number")
            self.hint_widget.update("Enter your international phone number with country code:")
            self.input_widget.placeholder = "+12025550101"
            self.input_widget.password = False

        elif self.step == "code":
            self.title_widget.update("Step 3: Verification Code")
            self.hint_widget.update(f"Code sent to {self.phone} via Telegram/SMS:")
            self.input_widget.placeholder = "12345"
            self.input_widget.password = False

        elif self.step == "password":
            self.title_widget.update("Step 4: Two-Step Verification (2FA)")
            self.hint_widget.update("Enter your 2FA password:")
            self.input_widget.placeholder = "Password"
            self.input_widget.password = True

        self.input_widget.focus()

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
                # In test or fake mode, proceed directly
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
        self.error_widget.update(f"⚠ {error}")
        self.status_widget.update("Please check the value and try again.")
        self.input_widget.focus()

    def on_login_success(self) -> None:
        self.dismiss(True)

    def action_cancel_login(self) -> None:
        if hasattr(self.gateway, "cancel_login"):
            self.gateway.cancel_login()
        self.dismiss(False)
