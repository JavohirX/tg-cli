"""Chrome widgets: Account strip, Folder strip, Status bar, and Footer.
All keyboard hints are read exclusively from commands.py.
"""

from __future__ import annotations

from typing import Sequence
from rich.text import Text
from textual.app import RenderResult
from textual.widget import Widget
from tg_cli.commands import get_footer_hints
from tg_cli.domain.models import Account, Folder


class AccountStrip(Widget):
    """Horizontal strip showing current accounts, numbers 1-9, unread badges, and proxy status."""

    DEFAULT_CSS = """
    AccountStrip {
        height: 1;
        width: 100%;
        background: $panel;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        accounts: Sequence[Account] | None = None,
        active_index: int = 0,
        unread_counts: dict[int, int] | None = None,
        proxy_status: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.accounts = list(accounts or [])
        self.active_index = active_index
        self.unread_counts = dict(unread_counts or {})
        self.proxy_status = proxy_status

    def set_accounts(
        self,
        accounts: Sequence[Account],
        active_index: int = 0,
        unread_counts: dict[int, int] | None = None,
        proxy_status: str | None = None,
    ) -> None:
        self.accounts = list(accounts)
        self.active_index = active_index
        if unread_counts is not None:
            self.unread_counts = dict(unread_counts)
        if proxy_status is not None:
            self.proxy_status = proxy_status
        self.refresh()

    def set_proxy_status(self, status: str | None) -> None:
        self.proxy_status = status
        self.refresh()

    def set_unread_counts(self, counts: dict[int, int]) -> None:
        self.unread_counts = dict(counts)
        self.refresh()

    def render(self) -> RenderResult:
        strip = Text()
        if not self.accounts:
            strip.append("No accounts", style="dim")
            return strip

        for i, acc in enumerate(self.accounts):
            is_active = (i == self.active_index)
            num_prefix = f"{i + 1}: " if i < 9 else ""
            unread = self.unread_counts.get(acc.user_id, 0)
            unread_badge = f" ({unread})" if unread > 0 else ""
            pill = f"[{num_prefix}{acc.label}{unread_badge}]"

            if is_active:
                strip.append(f" {pill} ", style="bold white on blue")
            else:
                if unread > 0:
                    strip.append(f" {pill} ", style="bold yellow")
                else:
                    strip.append(f" {pill} ", style="dim")
            strip.append(" ")

        if self.proxy_status:
            strip.append(f" {self.proxy_status} ", style="bold cyan on grey19")

        strip.append("  (1-9 / F2 to switch)", style="dim italic")
        return strip


class FolderStrip(Widget):
    """Horizontal tab strip for Telegram chat folders."""

    DEFAULT_CSS = """
    FolderStrip {
        height: 1;
        width: 100%;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        folders: Sequence[Folder] | None = None,
        active_folder_id: int | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.folders = list(folders or [])
        self.active_folder_id = active_folder_id  # None means "All"

    def set_folders(self, folders: Sequence[Folder], active_folder_id: int | None = None) -> None:
        self.folders = list(folders)
        self.active_folder_id = active_folder_id
        self.refresh()

    def render(self) -> RenderResult:
        strip = Text()

        # Synthetic 'All' tab
        is_all_active = (self.active_folder_id is None)
        if is_all_active:
            strip.append(" All ", style="bold white on dark_cyan")
        else:
            strip.append(" All ", style="dim")
        strip.append("  ")

        # Configured folders
        for folder in self.folders:
            is_active = (self.active_folder_id == folder.folder_id)
            if is_active:
                strip.append(f" {folder.title} ", style="bold white on dark_cyan")
            else:
                strip.append(f" {folder.title} ", style="dim")
            strip.append("  ")

        strip.append("( [ / ] tabs )", style="dim italic")
        return strip


class StatusBar(Widget):
    """Status bar showing sync state, inline y/n prompts, and undo toasts."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        width: 100%;
        background: $background;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.status_message: str = "Ready"
        self.prompt_message: str | None = None
        self.toast_message: str | None = None
        self.is_error: bool = False

    def set_status(self, message: str, is_error: bool = False) -> None:
        self.status_message = message
        self.is_error = is_error
        self.refresh()

    def set_prompt(self, prompt: str | None) -> None:
        self.prompt_message = prompt
        self.refresh()

    def set_toast(self, toast: str | None) -> None:
        self.toast_message = toast
        self.refresh()

    def render(self) -> RenderResult:
        text = Text()

        # Prompt has highest priority
        if self.prompt_message:
            text.append(f"[?] {self.prompt_message} ", style="bold yellow on grey23")
            return text

        # Toast notification has next priority
        if self.toast_message:
            text.append(f"[i] {self.toast_message} ", style="bold white on dark_magenta")
            return text

        # General status
        status_style = "bold red" if self.is_error else "dim cyan"
        text.append(self.status_message, style=status_style)
        return text


class FooterBar(Widget):
    """Dynamic footer reading shortcuts from commands.py and showing focus mode."""

    DEFAULT_CSS = """
    FooterBar {
        height: 1;
        width: 100%;
        background: $panel;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        context: str = "chats",
        is_write_mode: bool = False,
        char_count: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.context = context
        self.is_write_mode = is_write_mode
        self.char_count = char_count

    def update_state(
        self,
        context: str | None = None,
        is_write_mode: bool | None = None,
        char_count: int | None = None,
    ) -> None:
        if context is not None:
            self.context = context
        if is_write_mode is not None:
            self.is_write_mode = is_write_mode
        if char_count is not None:
            self.char_count = char_count
        self.refresh()

    def render(self) -> RenderResult:
        footer = Text()

        # Region tag: [LIST] or [WRITE]
        if self.is_write_mode:
            footer.append(" [WRITE] ", style="bold black on bright_yellow")
        else:
            footer.append(" [LIST] ", style="bold black on bright_cyan")
        footer.append(" ")

        hints = get_footer_hints(self.context, is_write_mode=self.is_write_mode)
        for key, title in hints:
            footer.append(f" {key} ", style="bold white on grey27")
            footer.append(f" {title}  ", style="dim")

        # Right-aligned char counter if in write mode
        if self.is_write_mode and self.char_count > 0:
            char_style = "bold red" if self.char_count > 4096 else "dim"
            counter = f" {self.char_count}/4096 chars "
            footer.append(counter, style=char_style)

        return footer
