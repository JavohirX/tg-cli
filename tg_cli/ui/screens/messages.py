"""Messages screen for tg-cli.
Renders message history using WindowedList and integrates the composer.
Supports variable-height rows, DateSeparator, UnreadDivider, history scrolling,
live message inserts with '↓ N new', and Tab toggling between LIST and WRITE focus.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable
from rich.console import Group, RenderableType
from rich.text import Text
from textual.actions import SkipAction
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.events import DescendantFocus, Key
from textual.screen import Screen
from textual.widgets import Input, Static
from pathlib import Path
from tg_cli.domain.models import (
    Account,
    Chat,
    ChatKind,
    DateSeparator,
    Draft,
    Message,
    MessageKind,
    SendState,
    Transcript,
    UnreadDivider,
)
from tg_cli.domain.render import (
    format_date_separator,
    message_bundle_flags,
    render_message,
    sender_header_flags,
)
from tg_cli.telegram.gateway import Gateway
from tg_cli.ui.chrome import FooterBar, StatusBar
from tg_cli.ui.clipboard import copy_text
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
    #filter-container {
        height: 1;
        width: 100%;
        display: none;
    }
    #filter-input {
        height: 1;
        border: none;
        padding: 0 1;
        background: $surface;
    }
    #message-list-container {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("tab", "toggle_focus", "Toggle List/Write", show=False),
        Binding("escape", "handle_escape", "Back/Defocus", show=False),
        Binding("/", "start_search", "Search", show=False),
        Binding("r", "reply_focused", "Reply", show=False),
        Binding("e", "edit_focused", "Edit", show=False),
        Binding("d", "delete_focused", "Delete", show=False),
        Binding("t", "transcribe_focused", "Transcribe", show=False),
        Binding("T", "retranscribe_focused", "Re-transcribe", show=False),
        Binding("y", "yank_focused", "Copy Text", show=False),
        Binding("z,Z", "toggle_expand", "Expand/Collapse", show=False, priority=True),
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
        self._message_cache: dict[int, Message] = {}
        self._show_sender: dict[int, bool] = {}
        self._show_time: dict[int, bool] = {}
        self._gap_after: dict[int, bool] = {}
        self._member_roles: dict[int, str] = {}
        self._is_loading_older: bool = False
        self._unseen_count: int = 0
        self.search_query: str = ""
        self._pending_confirm: tuple[str, Callable[[], None]] | None = None

        self.chat_header = Static("", id="chat-header")
        self.status_bar = StatusBar()
        self.footer_bar = FooterBar(context="messages", is_write_mode=False)
        self.composer = Composer(id="message-composer")
        self.filter_input = Input(placeholder="Search messages...", id="filter-input")

        self.message_list = WindowedList[Any](
            items=[],
            renderer=self._render_row_item,
            id_fn=lambda item: item.identity,
            is_selectable_fn=lambda item: isinstance(item, Message),
            item_height_fn=self._get_item_height,
            id="message-window-list",
            enable_digit_motion=False,
        )

    def compose(self) -> ComposeResult:
        with Vertical():
            yield self.chat_header
            with Container(id="filter-container"):
                yield self.filter_input
            with Container(id="message-list-container"):
                yield self.message_list
            yield self.composer
            yield self.status_bar
            yield self.footer_bar

    def on_mount(self) -> None:
        self._update_header()
        self.refresh_messages(initial=True)
        self.message_list.focus()
        self.call_after_refresh(self._scroll_to_latest)

        # Listen for real-time messages from gateway
        if hasattr(self.gateway, "register_listener"):
            self.gateway.register_listener(self._on_gateway_event)

        if not self._is_direct_chat():
            request_roles = getattr(self.gateway, "request_member_roles", None)
            if request_roles is not None:
                request_roles(self.account.user_id, self.chat.chat_id)

        # Mark chat as read initially
        self.gateway.mark_chats_read(self.account.user_id, [self.chat.chat_id], read=True)

        # Restore draft if exists
        draft = self.gateway.get_draft(self.account.user_id, self.chat.chat_id)
        if draft:
            self.composer.text = draft.text
            if draft.edit_message_id is not None:
                self.composer.set_edit_context(draft.edit_message_id, draft.text)
            elif draft.reply_to_id is not None:
                reply_msg = self._get_replied_message(draft.reply_to_id)
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
            elif event_type == "roles_changed":
                if data.get("error") or data.get("chat_id") != self.chat.chat_id:
                    return
                roles: dict[int, str] = {}
                for uid, role in (data.get("roles") or {}).items():
                    if role in ("admin", "owner"):
                        roles[int(uid)] = role
                self._member_roles = roles
                self.message_list.refresh()
            elif event_type == "status":
                msg = data.get("message", "")
                is_err = data.get("is_error", False)
                self.status_bar.set_status(msg, is_error=is_err)

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

        direct = self._is_direct_chat()
        self._show_sender = sender_header_flags(items, direct=direct)
        self._show_time, self._gap_after = message_bundle_flags(items)
        return items

    def _is_direct_chat(self) -> bool:
        """People and bots share the direct-message layout."""
        return self.chat.kind in (ChatKind.USER, ChatKind.BOT)

    def _message_layout(self, message: Message) -> tuple[bool, bool, bool]:
        """Direct chats color by author. Groups name only the first of a run."""
        direct = self._is_direct_chat()
        show_sender = self._show_sender.get(message.message_id, not direct)
        show_time = self._show_time.get(message.message_id, True)
        return direct, show_sender, show_time

    def _render_message_lines(
        self,
        item: Message,
        width: int,
        *,
        is_cursor: bool = False,
        is_selected: bool = False,
    ) -> list[Text]:
        """Render one message. A blank line follows only the last line of a bundle."""
        expanded = item.message_id in self.expanded_message_ids
        transcript_text = None
        if item.kind == MessageKind.VOICE:
            tr = self.gateway.get_transcript(self.account.user_id, self.chat.chat_id, item.message_id)
            if tr:
                transcript_text = tr.text if tr.text else f"[error: {tr.error}]"
        replied_msg = self._get_replied_message(item.reply_id) if item.reply_id else None
        direct, show_sender, show_time = self._message_layout(item)
        role = "" if direct else self._member_roles.get(item.sender_id, "")
        lines = render_message(
            message=item,
            width=width,
            expanded=expanded,
            transcript=transcript_text,
            is_cursor=is_cursor,
            is_selected=is_selected,
            replied_message=replied_msg,
            show_sender=show_sender,
            direct=direct,
            show_time=show_time,
            role=role,
        )
        if self._gap_after.get(item.message_id, True):
            lines.append(Text(""))
        return lines

    def _scroll_to_latest(self) -> None:
        if self.message_list.items:
            self.message_list.cursor = len(self.message_list.items) - 1
            self.message_list._adjust_cursor_to_selectable(-1)
            self.message_list._ensure_cursor_visible()
            self.message_list.refresh()

    def _get_replied_message(self, reply_id: int | None) -> Message | None:
        if reply_id is None:
            return None
        if reply_id in self._message_cache:
            return self._message_cache[reply_id]
        for m in self._all_messages:
            if m.message_id == reply_id:
                self._message_cache[reply_id] = m
                return m
        if hasattr(self.gateway, "get_message"):
            try:
                msg = self.gateway.get_message(self.account.user_id, self.chat.chat_id, reply_id)
                if msg:
                    self._message_cache[reply_id] = msg
                    return msg
            except Exception:
                pass
        return None

    def _get_item_height(self, item: Any) -> int:
        if isinstance(item, (DateSeparator, UnreadDivider)):
            return 1
        if isinstance(item, Message):
            width = max(10, getattr(self.message_list.size, "width", 80))
            return max(1, len(self._render_message_lines(item, width)))
        return 1

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
            lines = self._render_message_lines(
                item,
                width,
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
        try:
            messages = self.gateway.get_messages(
                account_id=self.account.user_id,
                chat_id=self.chat.chat_id,
                limit=50,
            )
        except Exception as e:
            self.status_bar.set_status(f"Chat unavailable: {e}", is_error=True)
            return

        self._all_messages = messages
        for m in messages:
            self._message_cache[m.message_id] = m
        items = self._build_stream_items(messages)
        self.message_list.set_items(items, keep_cursor=not initial and not jump_to_end)

        if initial or jump_to_end:
            if items:
                self.message_list.cursor = len(items) - 1
                self.message_list._adjust_cursor_to_selectable(-1)
                self.message_list._ensure_cursor_visible()
            self._unseen_count = 0
            self._update_header()

        if not messages and initial:
            self.status_bar.set_status("Loading messages...")
        else:
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

            for m in older:
                self._message_cache[m.message_id] = m
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
        if self.is_write_mode:
            self.is_write_mode = False
            self.footer_bar.update_state(is_write_mode=False)

        if event.index >= len(self.message_list.items) - 2:
            if self._unseen_count > 0:
                self._unseen_count = 0
                self._update_header()
                self.gateway.mark_chats_read(self.account.user_id, [self.chat.chat_id], read=True)
                self.status_bar.set_status(f"{len(self._all_messages)} messages loaded.")

    def on_windowed_list_clicked(self, event: WindowedList.Clicked) -> None:
        if self.is_write_mode:
            self._set_focus_mode(is_write=False)

    def on_windowed_list_text_copied(self, event: WindowedList.TextCopied) -> None:
        self.status_bar.set_status("Copied.")

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
        self._transcribe_focused(force=False)

    def action_retranscribe_focused(self) -> None:
        self._transcribe_focused(force=True)

    def _transcribe_focused(self, force: bool = False) -> None:
        if self.is_write_mode:
            return
        focused = self.message_list.get_focused_item()
        if not isinstance(focused, Message):
            return

        if focused.kind != MessageKind.VOICE:
            self.status_bar.set_status("Selected message is not a voice message.", is_error=True)
            return

        # Check existing transcript in cache if not forced
        if not force:
            existing = self.gateway.get_transcript(self.account.user_id, self.chat.chat_id, focused.message_id)
            if existing and existing.text:
                self.status_bar.set_status(f"Transcript (cached): {existing.text[:40]}")
                self.message_list.refresh()
                return

        # Perform transcription
        self.status_bar.set_status(f"Downloading voice message #{focused.message_id}...")

        temp_audio: Path | None = None
        try:
            temp_audio = self.gateway.download_voice_file(
                self.account.user_id,
                self.chat.chat_id,
                focused.message_id,
            )
            self.status_bar.set_status("Transcribing audio with Gemini...")

            from tg_cli.transcribe.gemini import GeminiTranscriber
            transcriber = getattr(self.app, "transcriber", None) or GeminiTranscriber()

            transcript_text = transcriber.transcribe_audio(temp_audio)

            record = Transcript(
                account_id=self.account.user_id,
                chat_id=self.chat.chat_id,
                message_id=focused.message_id,
                text=transcript_text,
                model="gemini",
                created_at=datetime.now(timezone.utc),
            )
            self.gateway.save_transcript(record)
            self.status_bar.set_status(f"Transcribed: {transcript_text[:50]}")
            self.message_list.refresh()
        except Exception as exc:
            err_msg = str(exc)
            fail_record = Transcript(
                account_id=self.account.user_id,
                chat_id=self.chat.chat_id,
                message_id=focused.message_id,
                error=err_msg,
                created_at=datetime.now(timezone.utc),
            )
            self.gateway.save_transcript(fail_record)
            self.status_bar.set_status(f"Transcription failed: {err_msg}", is_error=True)
            self.message_list.refresh()
        finally:
            if temp_audio is not None and temp_audio.exists():
                try:
                    temp_audio.unlink(missing_ok=True)
                except Exception:
                    pass

    def action_yank_focused(self) -> None:
        if self.is_write_mode:
            return
        focused = self.message_list.get_focused_item()
        if focused is not None and isinstance(focused, Message):
            if copy_text(self.app, focused.plain_text):
                self.status_bar.set_status("Copied message text to clipboard.")
            else:
                self.status_bar.set_status("Clipboard copy failed.", is_error=True)

    def action_toggle_expand(self) -> None:
        """Expand the message that shows the hint, or collapse it again.

        `z` and `Z` both toggle. While typing, the key is left for the composer.
        """
        if self.is_write_mode or self._pending_confirm is not None:
            raise SkipAction()
        target = self._message_to_expand()
        if target is None:
            self.status_bar.set_status("Nothing to expand.")
            return
        self._toggle_message_expansion(target)

    def _message_to_expand(self) -> Message | None:
        """Focused message if it is clamped or open; else the nearest visible hint."""
        focused = self.message_list.get_focused_item()
        if isinstance(focused, Message) and self._can_toggle_expand(focused):
            return focused
        best: Message | None = None
        best_dist: int | None = None
        cursor = self.message_list.cursor
        for idx in self._visible_message_indexes():
            item = self.message_list.items[idx]
            if isinstance(item, Message) and self._shows_expand_hint(item):
                dist = abs(idx - cursor)
                if best_dist is None or dist < best_dist:
                    best = item
                    best_dist = dist
        return best

    def _can_toggle_expand(self, message: Message) -> bool:
        if message.message_id in self.expanded_message_ids:
            return True
        return self._shows_expand_hint(message)

    def _shows_expand_hint(self, message: Message) -> bool:
        if message.message_id in self.expanded_message_ids:
            return False
        width = max(10, self.message_list.size.width or 0)
        lines = self._render_message_lines(message, width)
        return any("press 'z' to expand" in line.plain for line in lines)

    def _visible_message_indexes(self) -> list[int]:
        wl = self.message_list
        viewport = max(1, wl.size.height)
        found: list[int] = []
        y = 0
        for idx in range(wl.top_index, len(wl.items)):
            height = wl._get_height(idx)
            if idx == wl.top_index:
                height = max(0, height - wl.top_skip)
            if height <= 0:
                continue
            if isinstance(wl.items[idx], Message):
                found.append(idx)
            y += height
            if y >= viewport:
                break
        return found

    def _index_of_message(self, message: Message) -> int | None:
        for idx, item in enumerate(self.message_list.items):
            if isinstance(item, Message) and item.message_id == message.message_id:
                return idx
        return None

    def _toggle_message_expansion(self, message: Message) -> None:
        wl = self.message_list
        index = self._index_of_message(message)
        if index is None:
            return
        mid = message.message_id
        moved = wl.cursor != index
        if mid in self.expanded_message_ids:
            self.expanded_message_ids.remove(mid)
            wl.cursor = index
            wl.top_skip = 0
            wl._ensure_cursor_visible()
        else:
            width = max(10, wl.size.width or 0)
            collapsed = self._render_message_lines(message, width)
            before_h = len(collapsed)
            hint_line = next(
                (
                    i for i, line in enumerate(collapsed)
                    if "press 'z' to expand" in line.plain
                ),
                max(0, before_h - 1),
            )
            message_top = 0
            for idx in range(wl.top_index, index):
                height = wl._get_height(idx)
                if idx == wl.top_index:
                    height = max(0, height - wl.top_skip)
                message_top += height
            hint_y = message_top + hint_line
            self.expanded_message_ids.add(mid)
            self._place_expanded_message(index, before_h, hint_line, hint_y)
        if moved:
            wl.post_message(wl.CursorMoved(wl.cursor, wl.get_focused_item()))
        wl.refresh()

    def _place_expanded_message(
        self, index: int, before_h: int, hint_line: int, hint_y: int
    ) -> None:
        """Keep the first newly revealed line on screen."""
        wl = self.message_list
        wl.cursor = index
        after_h = wl._get_height(index)
        viewport = max(1, wl.size.height)
        if after_h <= viewport:
            wl.top_skip = 0
            wl._ensure_cursor_visible()
            return
        wl.top_index = index
        max_skip = max(0, after_h - viewport)
        if 0 <= hint_y < viewport:
            desired = hint_line - hint_y
        else:
            desired = before_h - 3
        wl.top_skip = min(max(0, desired), max_skip)

    def action_start_search(self) -> None:
        if self.is_write_mode:
            return
        container = self.query_one("#filter-container")
        container.display = True
        self.filter_input.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self.search_query = event.value.strip()
            self._apply_search_filter(jump=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter-input":
            query = event.value.strip()
            if query:
                # Query gateway for messages outside window
                older = self.gateway.get_messages(
                    account_id=self.account.user_id,
                    chat_id=self.chat.chat_id,
                    limit=50,
                    search_query=query,
                )
                if older:
                    existing_ids = {m.message_id for m in self._all_messages}
                    new_msgs = [m for m in older if m.message_id not in existing_ids]
                    if new_msgs:
                        self._all_messages = sorted(new_msgs + self._all_messages, key=lambda m: m.message_id)
            self._apply_search_filter(jump=True)
            self.message_list.focus()

    def _apply_search_filter(self, jump: bool = False) -> None:
        if not self.search_query:
            items = self._build_stream_items(self._all_messages)
            self.message_list.set_items(items, keep_cursor=True)
            return

        q = self.search_query.lower()
        matched = [
            m for m in self._all_messages
            if q in m.plain_text.lower() or q in m.sender_name.lower()
        ]
        items = self._build_stream_items(matched)
        self.message_list.set_items(items, keep_cursor=not jump)
        if jump and items:
            self.message_list.cursor = 0
            self.message_list._adjust_cursor_to_selectable(1)
            self.message_list._ensure_cursor_visible()
            self.status_bar.set_status(f"Found {len(matched)} match(es) for '{self.search_query}'")
        elif not matched:
            self.status_bar.set_status(f"No messages matching '{self.search_query}'")

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
            elif event.character in ("n", "N") or event.key == "escape":
                self._pending_confirm = None
                self.status_bar.set_prompt(None)
                self.status_bar.set_status("Action cancelled.")
            event.prevent_default()
            event.stop()
            return

        if not self.is_write_mode:
            container = self.query_one("#filter-container")
            if not container.display:
                key_char = event.character or event.key
                if key_char and key_char in "123456789":
                    idx = int(key_char) - 1
                    accounts = self.gateway.get_accounts()
                    if 0 <= idx < len(accounts):
                        target_acc = accounts[idx]
                        self.app.pop_screen()
                        if hasattr(self.app.screen, "action_switch_account_index"):
                            self.app.screen.action_switch_account_index(idx)
                        elif hasattr(self.app, "open_account"):
                            self.app.open_account(target_acc)
                        event.prevent_default()
                        event.stop()
                        return

    def action_handle_escape(self) -> None:
        # Esc stack rule:
        # 1. Confirmation prompt active
        if self._pending_confirm is not None:
            self._pending_confirm = None
            self.status_bar.set_prompt(None)
            return

        # 2. If in write mode, defocus composer or clear reply/edit
        if self.is_write_mode:
            if not self.composer.clear_context():
                self._set_focus_mode(is_write=False)
            return

        # 3. Search active
        container = self.query_one("#filter-container")
        if container.display:
            container.display = False
            self.search_query = ""
            self.filter_input.value = ""
            self._apply_search_filter(jump=False)
            self.message_list.focus()
            return

        # 4. If selection active in message list, clear selection
        if self.message_list.clear_selection():
            return

        # 5. Dragged text
        if self.message_list.clear_text_selection():
            return

        # 6. Pop back to chat list
        self.app.pop_screen()

    def action_show_help(self) -> None:
        from tg_cli.ui.screens.help import HelpScreen
        self.app.push_screen(HelpScreen(context="messages"))

    def action_show_palette(self) -> None:
        from tg_cli.ui.screens.palette import CommandPalette
        self.app.push_screen(
            CommandPalette(context="messages"),
            callback=self._handle_palette_command,
        )

    def _handle_palette_command(self, cmd: Any) -> None:
        if cmd is None:
            return

        def execute() -> None:
            match cmd.id:
                case "reply_message":
                    self.action_reply_focused()
                case "edit_message":
                    self.action_edit_focused()
                case "delete_message":
                    self.action_delete_focused()
                case "transcribe_voice":
                    self.action_transcribe_focused()
                case "retranscribe_voice":
                    self.action_retranscribe_focused()
                case "yank_text":
                    self.action_yank_focused()
                case "toggle_expand":
                    self.action_toggle_expand()
                case "filter":
                    self.action_start_search()
                case "focus_toggle":
                    self.action_toggle_focus()
                case "toggle_proxy":
                    from tg_cli.config import get_proxy_config, probe_proxy_latency, toggle_proxy
                    new_state = toggle_proxy()
                    if new_state:
                        cfg = get_proxy_config()
                        lat = probe_proxy_latency(cfg)
                        lat_str = f" ({lat}ms)" if lat is not None else ""
                        self.status_bar.set_toast(f"Proxy enabled: {cfg.get('type')} {cfg.get('addr')}:{cfg.get('port')}{lat_str}")
                    else:
                        self.status_bar.set_toast("Proxy disabled.")
                case "help":
                    self.action_show_help()

        if getattr(cmd, "destructive", False):
            self._request_confirm(
                f"Run '{cmd.title}'? (y/n)",
                execute,
            )
        else:
            execute()
