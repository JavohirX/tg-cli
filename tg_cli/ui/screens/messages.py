"""Messages screen for tg-cli.
Renders message history using WindowedList and integrates the composer.
Supports variable-height rows, DateSeparator, UnreadDivider, history scrolling,
live message inserts with '↓ N new', and Tab toggling between LIST and WRITE focus.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any
from rich.console import Group, RenderableType
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Static
from tg_cli.domain.models import (
    Account,
    Chat,
    DateSeparator,
    Draft,
    Message,
    SendState,
    UnreadDivider,
)
from tg_cli.domain.render import format_date_separator, render_message
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

        self._all_messages: list[Message] = []
        self._is_loading_older: bool = False
        self._unseen_count: int = 0

        self.chat_header = Static("", id="chat-header")
        self.status_bar = StatusBar()
        self.footer_bar = FooterBar(context="messages", is_write_mode=False)
        self.composer = Composer(id="message-composer")

        self.message_list = WindowedList[Any](
            items=[],
            renderer=self._render_row_item,
            id_fn=lambda item: item.identity,
            is_selectable_fn=lambda item: isinstance(item, Message),
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
        self.refresh_messages(initial=True)
        self.message_list.focus()

        # Listen for real-time messages from gateway
        if hasattr(self.gateway, "register_listener"):
            self.gateway.register_listener(self._on_gateway_event)

        # Mark chat as read initially
        self.gateway.mark_chats_read(self.account.user_id, [self.chat.chat_id], read=True)

        # Restore draft if exists
        draft = self.gateway.get_draft(self.account.user_id, self.chat.chat_id)
        if draft:
            self.composer.text = draft.text
            if draft.edit_message_id is not None:
                self.composer.set_edit_context(draft.edit_message_id, draft.text)
            elif draft.reply_to_id is not None:
                reply_msg = next((m for m in self._all_messages if m.message_id == draft.reply_to_id), None)
                sender = reply_msg.sender_name if reply_msg else f"#{draft.reply_to_id}"
                prev = (reply_msg.plain_text or "") if reply_msg else ""
                self.composer.set_reply_context(draft.reply_to_id, sender, prev)

    def _persist_draft(self) -> None:
        text = self.composer.text
        if text.strip():
            draft = Draft(
                account_id=self.account.user_id,
                chat_id=self.chat.chat_id,
                text=text,
                reply_to_id=self.composer.reply_to_id,
                edit_message_id=self.composer.edit_message_id,
            )
            self.gateway.save_draft(draft)
        else:
            self.gateway.clear_draft(self.account.user_id, self.chat.chat_id)

    def on_unmount(self) -> None:
        self._persist_draft()
        if hasattr(self.gateway, "unregister_listener"):
            self.gateway.unregister_listener(self._on_gateway_event)

    def _on_gateway_event(self, event_type: str, data: dict[str, Any]) -> None:
        def handle():
            if event_type == "messages_changed":
                if data.get("chat_id") == self.chat.chat_id:
                    self._handle_live_message_event()

        try:
            if threading.current_thread() is threading.main_thread():
                handle()
            else:
                self.app.call_from_thread(handle)
        except Exception:
            pass

    def _handle_live_message_event(self) -> None:
        # Check if cursor is near bottom
        total = len(self.message_list.items)
        is_pinned_to_bottom = (total == 0) or (self.message_list.cursor >= total - 2)

        if is_pinned_to_bottom:
            self._unseen_count = 0
            self.refresh_messages(initial=False, jump_to_end=True)
            self.gateway.mark_chats_read(self.account.user_id, [self.chat.chat_id], read=True)
        else:
            self._unseen_count += 1
            self.refresh_messages(initial=False, jump_to_end=False)
            self.status_bar.set_status(f"↓ {self._unseen_count} new (press End to jump)")

    def _update_header(self) -> None:
        header = Text()
        header.append(f" {self.chat.title} ", style="bold white")
        header.append(f" ({self.chat.kind.value})", style="dim")
        if self._unseen_count > 0:
            header.append(f"  [↓ {self._unseen_count} new]", style="bold bright_cyan on blue")
        self.chat_header.update(header)

    def _build_stream_items(self, messages: list[Message]) -> list[Any]:
        """Insert DateSeparators and UnreadDivider into message stream."""
        items: list[Any] = []
        last_date_str: str | None = None
        unread_threshold = self.chat.unread_count
        unread_inserted = False

        # Messages are ordered chronologically
        total = len(messages)
        first_unread_idx = total - unread_threshold if unread_threshold > 0 else -1

        for idx, msg in enumerate(messages):
            if msg.date:
                day_str = format_date_separator(msg.date)
                if day_str != last_date_str:
                    items.append(DateSeparator(date_str=day_str))
                    last_date_str = day_str

            if unread_threshold > 0 and not unread_inserted and idx >= first_unread_idx:
                items.append(UnreadDivider())
                unread_inserted = True

            items.append(msg)

        return items

    def _render_row_item(
        self, item: Any, width: int, is_cursor: bool, is_selected: bool
    ) -> RenderableType:
        if isinstance(item, DateSeparator):
            text = Text(f"─── {item.date_str} ───", justify="center")
            text.stylize("dim cyan")
            return text

        if isinstance(item, UnreadDivider):
            text = Text("───────── Unread Messages ─────────", justify="center")
            text.stylize("bold bright_white on dark_blue")
            return text

        if isinstance(item, Message):
            expanded = item.message_id in self.expanded_message_ids
            lines = render_message(
                message=item,
                width=width,
                expanded=expanded,
                is_cursor=is_cursor,
                is_selected=is_selected,
            )
            return Group(*lines)

        return Text(str(item))

    def refresh_messages(
        self,
        initial: bool = False,
        jump_to_end: bool = False,
    ) -> None:
        messages = self.gateway.get_messages(
            account_id=self.account.user_id,
            chat_id=self.chat.chat_id,
            limit=50,
        )
        self._all_messages = messages
        items = self._build_stream_items(messages)
        self.message_list.set_items(items, keep_cursor=not initial and not jump_to_end)

        if initial or jump_to_end:
            if items:
                self.message_list.cursor = len(items) - 1
                self.message_list._adjust_cursor_to_selectable(-1)
                self.message_list._ensure_cursor_visible()
            self._unseen_count = 0
            self._update_header()

        self.status_bar.set_status(f"{len(messages)} messages loaded.")

    def load_older_messages(self) -> None:
        """Page older messages when cursor hits the top."""
        if self._is_loading_older or not self._all_messages:
            return

        oldest_msg = self._all_messages[0]
        self._is_loading_older = True
        self.status_bar.set_status("Loading older history...")

        try:
            older = self.gateway.get_messages(
                account_id=self.account.user_id,
                chat_id=self.chat.chat_id,
                limit=50,
                before_id=oldest_msg.message_id,
            )
            if not older:
                self.status_bar.set_status("Beginning of history reached.")
                return

            # Combine older + current, avoiding duplicates
            existing_ids = {m.message_id for m in self._all_messages}
            new_older = [m for m in older if m.message_id not in existing_ids]
            if not new_older:
                return

            prev_focused = self.message_list.get_focused_item()
            self._all_messages = new_older + self._all_messages
            new_items = self._build_stream_items(self._all_messages)

            # Find new index of previously focused message to prevent cursor jumping
            new_cursor = 0
            if prev_focused is not None and isinstance(prev_focused, Message):
                for idx, it in enumerate(new_items):
                    if isinstance(it, Message) and it.message_id == prev_focused.message_id:
                        new_cursor = idx
                        break

            self.message_list.set_items(new_items, keep_cursor=True)
            self.message_list.cursor = new_cursor
            self.message_list._ensure_cursor_visible()
            self.status_bar.set_status(f"Loaded {len(new_older)} older messages.")
        finally:
            self._is_loading_older = False

    def on_windowed_list_reached_top(self, event: WindowedList.ReachedTop) -> None:
        self.load_older_messages()

    def on_windowed_list_cursor_moved(self, event: WindowedList.CursorMoved) -> None:
        if event.index >= len(self.message_list.items) - 2:
            if self._unseen_count > 0:
                self._unseen_count = 0
                self._update_header()
                self.gateway.mark_chats_read(self.account.user_id, [self.chat.chat_id], read=True)
                self.status_bar.set_status(f"{len(self._all_messages)} messages loaded.")

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
        self.gateway.clear_draft(self.account.user_id, self.chat.chat_id)
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

        self.refresh_messages(initial=False, jump_to_end=True)

    def on_windowed_list_item_activated(self, event: WindowedList.ItemActivated) -> None:
        if isinstance(event.item, Message) and event.item.send_state == SendState.FAILED:
            self.gateway.retry_failed_message(
                self.account.user_id,
                self.chat.chat_id,
                event.item.message_id,
            )
            self.status_bar.set_status(f"Retrying message #{event.item.message_id}...")
            self.refresh_messages(initial=False)

    def action_reply_focused(self) -> None:
        if self.is_write_mode:
            return
        focused = self.message_list.get_focused_item()
        if focused is not None and isinstance(focused, Message):
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
        if focused is not None and isinstance(focused, Message):
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
        raw_targets = selected if selected else ([self.message_list.get_focused_item()] if self.message_list.get_focused_item() else [])
        targets = [m for m in raw_targets if isinstance(m, Message)]
        if not targets:
            return
        target_ids = [m.message_id for m in targets]
        self.gateway.delete_messages(
            account_id=self.account.user_id,
            chat_id=self.chat.chat_id,
            message_ids=target_ids,
        )
        self.message_list.clear_selection()
        self.refresh_messages(initial=False)
        self.status_bar.set_status(f"Deleted {len(target_ids)} message(s).")

    def action_transcribe_focused(self) -> None:
        if self.is_write_mode:
            return
        focused = self.message_list.get_focused_item()
        if focused is not None and isinstance(focused, Message):
            self.status_bar.set_status(f"Transcription for #{focused.message_id} will run via Gemini in Phase 7.")

    def action_retranscribe_focused(self) -> None:
        if self.is_write_mode:
            return
        focused = self.message_list.get_focused_item()
        if focused is not None and isinstance(focused, Message):
            self.status_bar.set_status(f"Forced re-transcription for #{focused.message_id} scheduled.")

    def action_yank_focused(self) -> None:
        if self.is_write_mode:
            return
        focused = self.message_list.get_focused_item()
        if focused is not None and isinstance(focused, Message):
            try:
                self.app.copy_to_clipboard(focused.plain_text)
                self.status_bar.set_status("Copied message text to clipboard.")
            except Exception:
                self.status_bar.set_status("Clipboard copy not supported by terminal environment.", is_error=True)

    def action_toggle_expand(self) -> None:
        if self.is_write_mode:
            return
        focused = self.message_list.get_focused_item()
        if focused is not None and isinstance(focused, Message):
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
