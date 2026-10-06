"""User Profile & Status Management screen for tg-cli.
Inspects account profile details (Name, Username, Phone, Status, Bio).
Allows editing bio and username directly from the terminal.
"""

from __future__ import annotations

from typing import Any
from rich.panel import Panel
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.events import Key
from textual.screen import Screen
from textual.widgets import Input, Static
from tg_cli.domain.models import Account, UserProfile
from tg_cli.telegram.gateway import Gateway
from tg_cli.ui.chrome import FooterBar, StatusBar


class ProfileScreen(Screen):
    """Screen for viewing and editing Telegram user profile & bio."""

    DEFAULT_CSS = """
    ProfileScreen {
        layout: vertical;
        background: $background;
    }
    #profile-header {
        height: 3;
        padding: 1 2;
        background: $panel;
        color: $text;
        text-style: bold;
    }
    #profile-container {
        height: 1fr;
        padding: 1 2;
    }
    #profile-details {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }
    #profile-edit-box {
        width: 100%;
        height: auto;
        display: none;
        margin-top: 1;
    }
    #profile-edit-label {
        height: 1;
        color: $accent;
        text-style: bold;
    }
    #profile-edit-input {
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("b", "start_edit_bio", "Edit Bio", show=False),
        Binding("B", "start_edit_bio", "Edit Bio", show=False),
        Binding("u", "start_edit_username", "Edit Username", show=False),
        Binding("U", "start_edit_username", "Edit Username", show=False),
        Binding("escape", "handle_escape", "Back", show=False),
        Binding("?", "show_help", "Help", show=False),
        Binding("ctrl+k", "show_palette", "Palette", show=False),
    ]

    def __init__(self, gateway: Gateway, account: Account, **kwargs) -> None:
        super().__init__(**kwargs)
        self.gateway = gateway
        self.account = account
        self.profile: UserProfile | None = None
        self._edit_mode: str | None = None  # 'bio' or 'username'

        self.header_widget = Static(
            f"User Profile & Status — {self.account.label}", id="profile-header"
        )
        self.details_widget = Static("", id="profile-details")
        self.details_widget.can_focus = True
        self.edit_label = Static("", id="profile-edit-label")
        self.edit_input = Input(id="profile-edit-input")
        self.status_bar = StatusBar()
        self.footer_bar = FooterBar(context="profile")

    def compose(self) -> ComposeResult:
        with Vertical():
            yield self.header_widget
            with Container(id="profile-container"):
                yield self.details_widget
                with Vertical(id="profile-edit-box"):
                    yield self.edit_label
                    yield self.edit_input
            yield self.status_bar
            yield self.footer_bar

    def on_mount(self) -> None:
        self.refresh_profile()
        self.details_widget.focus()

    def refresh_profile(self) -> None:
        if hasattr(self.gateway, "get_user_profile"):
            self.profile = self.gateway.get_user_profile(self.account.user_id)
        else:
            self.profile = UserProfile(
                user_id=self.account.user_id,
                first_name=self.account.display_name or "User",
                username=self.account.username,
                phone=self.account.phone,
                bio="",
                status_emoji="",
                is_online=True,
            )

        self._render_details()
        self.status_bar.set_status("Press 'b' to edit bio · 'u' to edit username · Esc to return.")

    def _render_details(self) -> None:
        if not self.profile:
            return

        text = Text()
        text.append("Display Name:  ", style="bold cyan")
        full_name = f"{self.profile.first_name} {self.profile.last_name}".strip() or self.account.label
        text.append(f"{full_name}\n", style="white")

        text.append("Username:      ", style="bold cyan")
        user_str = f"@{self.profile.username}" if self.profile.username else "(not set)"
        text.append(f"{user_str}\n", style="yellow" if self.profile.username else "dim")

        text.append("Phone Number:  ", style="bold cyan")
        text.append(f"{self.profile.phone or '(hidden)'}\n", style="white")

        text.append("Online Status: ", style="bold cyan")
        online_str = "[Online]" if self.profile.is_online else "[Offline]"
        text.append(f"{online_str}\n\n", style="bold green" if self.profile.is_online else "dim")

        text.append("Bio / About:\n", style="bold cyan")
        bio_str = self.profile.bio or "(No bio set)"
        text.append(f"  {bio_str}\n", style="italic white" if self.profile.bio else "dim italic")

        self.details_widget.update(Panel(text, title=f"Account #{self.account.user_id}", border_style="cyan"))

    def action_start_edit_bio(self) -> None:
        if self._edit_mode is not None:
            return
        self._edit_mode = "bio"
        edit_box = self.query_one("#profile-edit-box")
        edit_box.display = True
        self.edit_label.update("Editing Bio (Press Enter to save, Esc to cancel):")
        self.edit_input.value = self.profile.bio if self.profile else ""
        self.edit_input.placeholder = "Enter your new bio..."
        self.edit_input.focus()
        self.status_bar.set_status("Editing bio. Press Enter to save.")

    def action_start_edit_username(self) -> None:
        if self._edit_mode is not None:
            return
        self._edit_mode = "username"
        edit_box = self.query_one("#profile-edit-box")
        edit_box.display = True
        self.edit_label.update("Editing Username (without @, press Enter to save, Esc to cancel):")
        self.edit_input.value = self.profile.username if self.profile else ""
        self.edit_input.placeholder = "new_username"
        self.edit_input.focus()
        self.status_bar.set_status("Editing username. Press Enter to save.")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        if self._edit_mode == "bio":
            if hasattr(self.gateway, "update_user_profile"):
                self.gateway.update_user_profile(self.account.user_id, bio=val)
            if self.profile:
                self.profile.bio = val
            self.status_bar.set_status("Bio updated successfully.")

        elif self._edit_mode == "username":
            clean_username = val.lstrip("@")
            if hasattr(self.gateway, "update_user_profile"):
                self.gateway.update_user_profile(self.account.user_id, username=clean_username)
            if self.profile:
                self.profile.username = clean_username
            self.account.username = clean_username
            self.status_bar.set_status(f"Username updated to @{clean_username}.")

        self._close_edit_box()
        self._render_details()

    def _close_edit_box(self) -> None:
        self._edit_mode = None
        edit_box = self.query_one("#profile-edit-box")
        edit_box.display = False
        self.details_widget.focus()

    def on_key(self, event: Key) -> None:
        if self._edit_mode is None:
            key_char = (event.character or event.key or "").lower()
            if key_char == "b":
                self.action_start_edit_bio()
                event.prevent_default()
                event.stop()
            elif key_char == "u":
                self.action_start_edit_username()
                event.prevent_default()
                event.stop()

    def action_handle_escape(self) -> None:
        if self._edit_mode is not None:
            self._close_edit_box()
            self.status_bar.set_status("Edit cancelled.")
            return
        self.app.pop_screen()

    def action_show_help(self) -> None:
        from tg_cli.ui.screens.help import HelpScreen
        self.app.push_screen(HelpScreen(context="profile"))

    def action_show_palette(self) -> None:
        from tg_cli.ui.screens.palette import CommandPalette
        self.app.push_screen(CommandPalette(context="global"))
