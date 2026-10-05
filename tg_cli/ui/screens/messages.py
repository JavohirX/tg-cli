"""Messages screen for tg-cli.
Renders message history using WindowedList and integrates the composer.
Supports LIST and WRITE focus regions with Tab toggle.
"""

from __future__ import annotations

from typing import Any
from rich.console import Group, RenderableType
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Static
from tg_cli.domain.models import Account, Chat, Message
from tg_cli.domain.render import render_message
from tg_cli.telegram.gateway import Gateway
from tg_cli.ui.chrome import FooterBar, StatusBar
from tg_cli.ui.composer import Composer
from tg_cli.ui.window import WindowedList


class MessagesScreen(Screen):
    """Message view and composition screen."""

    DEFAULT_CSS = """
    MessagesScreen {
        layout: vertical;
        background: $background;
    }
    #chat-header {
        height: 1;
        width: 100%;
        background: $panel;
        color: $text;
        padding: 0 1;
    }
    #message-list-container {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("tab", "toggle_focus", "Toggle List/Write", show=False),
        Binding("escape", "handle_escape", "Back/Defocus", show=False),
        Binding("r", "reply_focused", "Reply", show=False),
        Binding("e", "edit_focused", "Edit", show=False),
        Binding("d", "delete_focused", "Delete", show=False),
        Binding("t", "transcribe_focused", "Transcribe", show=False),
        Binding("T", "retranscribe_focused", "Re-transcribe", show=False),
        Binding("y", "yank_focused", "Copy Text", show=False),
        Binding("z", "toggle_expand", "Expand/Collapse", show=False),
        Binding("?", "show_help", "Help", show=False),
        Binding("f1", "show_help", "Help", show=False),
        Binding("ctrl+k", "show_palette", "Palette", show=False),
    ]

    def __init__(
        self,
        gateway: Gateway,
        account: Account,
        chat: Chat,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.gateway = gateway
        self.account = account
        self.chat = chat
        self.is_write_mode: bool = False
        self.expanded_message_ids: set[int] = set()

        self.chat_header = Static("", id="chat-header")
        self.status_bar = StatusBar()
        self.footer_bar = FooterBar(context="messages", is_write_mode=False)
        self.composer = Composer(id="message-composer")

        self.message_list = WindowedList[Message](
            items=[],
            renderer=self._render_message_item,
            id_fn=lambda m: m.message_id,
            id="message-window-list",
        )

    def compose(self) -> ComposeResult:
        with Vertical():
            yield self.chat_header
            with Container(id="message-list-container"):
                yield self.message_list
            yield self.composer
            yield self.status_bar
            yield self.footer_bar

    def on_mount(self) -> None:
        self._update_header()
        self.refresh_messages()
        # Initial focus is list mode
        self.message_list.focus()
        # Jump cursor to bottom (newest message)
        if self.message_list.items:
            self.message_list.cursor = len(self.message_list.items) - 1
            self.message_list._ensure_cursor_visible()

    def _update_header(self) -> None:
        header = Text()
        header.append(f" {self.chat.title} ", style="bold white")
        header.append(f" ({self.chat.kind.value})", style="dim")
        self.chat_header.update(header)

    def _render_message_item(
        self, message: Message, width: int, is_cursor: bool, is_selected: bool
    ) -> RenderableType:
        expanded = message.message_id in self.expanded_message_ids
        lines = render_message(
            message=message,
            width=width,
            expanded=expanded,
            is_cursor=is_cursor,
            is_selected=is_selected,
        )
        return Group(*lines)

    def refresh_messages(self) -> None:
        messages = self.gateway.get_messages(
            account_id=self.account.user_id,
            chat_id=self.chat.chat_id,
            limit=100,
        )
        self.message_list.set_items(messages)
        self.status_bar.set_status(f"{len(messages)} messages loaded.")

    def action_toggle_focus(self) -> None:
        """Switch focus between list and composer."""
        if self.is_write_mode:
            self._set_focus_mode(is_write=False)
        else:
            self._set_focus_mode(is_write=True)

    def _set_focus_mode(self, is_write: bool) -> None:
        self.is_write_mode = is_write
        if is_write:
            self.composer.focus_input()
        else:
            self.message_list.focus()
        self.footer_bar.update_state(is_write_mode=self.is_write_mode)

    def on_composer_cancel_requested(self, event: Composer.CancelRequested) -> None:
        self._set_focus_mode(is_write=False)

    def on_composer_char_count_changed(self, event: Composer.CharCountChanged) -> None:
        self.footer_bar.update_state(char_count=event.count)

    def on_composer_error_reported(self, event: Composer.ErrorReported) -> None:
        self.status_bar.set_status(event.error, is_error=True)

    def on_composer_message_submitted(self, event: Composer.MessageSubmitted) -> None:
        if event.edit_message_id is not None:
            self.gateway.edit_message(
                account_id=self.account.user_id,
                chat_id=self.chat.chat_id,
                message_id=event.edit_message_id,
                text=event.text,
            )
            self.status_bar.set_status(f"Edited message #{event.edit_message_id}")
        else:
            new_msg = self.gateway.send_message(
                account_id=self.account.user_id,
                chat_id=self.chat.chat_id,
                text=event.text,
                reply_to_id=event.reply_to_id,
            )
            self.status_bar.set_status(f"Sent message #{new_msg.message_id}")

        self.refresh_messages()
        # Move cursor to newest message
        if self.message_list.items:
            self.message_list.cursor = len(self.message_list.items) - 1
            self.message_list._ensure_cursor_visible()
            self.message_list.refresh()

    def action_reply_focused(self) -> None:
        if self.is_write_mode:
            return
        focused = self.message_list.get_focused_item()
        if focused is not None:
            preview = focused.plain_text or f"[{focused.kind.value}]"
            self.composer.set_reply_context(
                message_id=focused.message_id,
                sender_name=focused.sender_name,
                preview=preview,
            )
            self._set_focus_mode(is_write=True)

    def action_edit_focused(self) -> None:
        if self.is_write_mode:
            return
        focused = self.message_list.get_focused_item()
        if focused is not None:
            if not focused.outgoing:
                self.status_bar.set_status("Cannot edit: message was sent by someone else.", is_error=True)
                return
            self.composer.set_edit_context(
                message_id=focused.message_id,
                current_text=focused.plain_text,
            )
            self._set_focus_mode(is_write=True)

    def action_delete_focused(self) -> None:
        if self.is_write_mode:
            return
        selected = self.message_list.get_selected_items()
        targets = selected if selected else ([self.message_list.get_focused_item()] if self.message_list.get_focused_item() else [])
        if not targets:
            return
        target_ids = [m.message_id for m in targets if m is not None]
        self.gateway.delete_messages(
            account_id=self.account.user_id,
            chat_id=self.chat.chat_id,
            message_ids=target_ids,
        )
        self.message_list.clear_selection()
        self.refresh_messages()
        self.status_bar.set_status(f"Deleted {len(target_ids)} message(s).")

    def action_transcribe_focused(self) -> None:
        if self.is_write_mode:
            return
        focused = self.message_list.get_focused_item()
        if focused is not None:
            self.status_bar.set_status(f"Transcription for #{focused.message_id} will run via Gemini in Phase 7.")

    def action_retranscribe_focused(self) -> None:
        if self.is_write_mode:
            return
        focused = self.message_list.get_focused_item()
        if focused is not None:
            self.status_bar.set_status(f"Forced re-transcription for #{focused.message_id} scheduled.")

    def action_yank_focused(self) -> None:
        if self.is_write_mode:
            return
        focused = self.message_list.get_focused_item()
        if focused is not None:
            text_to_copy = focused.plain_text
            try:
                self.app.copy_to_clipboard(text_to_copy)
                self.status_bar.set_status("Copied message text to clipboard.")
            except Exception:
                self.status_bar.set_status("Clipboard copy not supported by terminal environment.", is_error=True)

    def action_toggle_expand(self) -> None:
        if self.is_write_mode:
            return
        focused = self.message_list.get_focused_item()
        if focused is not None:
            mid = focused.message_id
            if mid in self.expanded_message_ids:
                self.expanded_message_ids.remove(mid)
            else:
                self.expanded_message_ids.add(mid)
            self.message_list.refresh()

    def action_handle_escape(self) -> None:
        # Esc stack rule:
        # 1. If in write mode, defocus composer or clear reply/edit
        if self.is_write_mode:
            if not self.composer.clear_context():
                self._set_focus_mode(is_write=False)
            return

        # 2. If selection active in message list, clear selection
        if self.message_list.clear_selection():
            return

        # 3. Pop back to chat list
        self.app.pop_screen()

    def action_show_help(self) -> None:
        from tg_cli.ui.screens.help import HelpScreen
        self.app.push_screen(HelpScreen(context="messages"))

    def action_show_palette(self) -> None:
        from tg_cli.ui.screens.palette import CommandPalette
        self.app.push_screen(CommandPalette(context="messages"))
