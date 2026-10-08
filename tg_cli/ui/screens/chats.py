"""Chats list screen for tg-cli.
Renders virtualized chat list, folders, accounts strip, filter bar, and status/footer.
"""

from __future__ import annotations

import time
from typing import Any, Callable
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.events import Key
from textual.screen import Screen
from textual.widgets import Input, Static
from tg_cli.domain.models import Account, Chat, Folder
from tg_cli.domain.render import render_chat_row
from tg_cli.telegram.gateway import Gateway
from tg_cli.ui.chrome import AccountStrip, FolderStrip, FooterBar, StatusBar
from tg_cli.ui.window import WindowedList

ARCHIVE_CHAT_ID = -1000


class ChatsScreen(Screen):
    """Main chat list screen."""

    DEFAULT_CSS = """
    ChatsScreen {
        layout: vertical;
        background: $background;
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
    #chat-list-container {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("enter", "open_focused", "Open", show=False),
        Binding("/", "start_filter", "Filter", show=False),
        Binding("[", "prev_folder", "Prev Folder", show=False),
        Binding("]", "next_folder", "Next Folder", show=False),
        Binding("a", "toggle_archive", "Archive", show=False),
        Binding("m", "toggle_mute", "Mute", show=False),
        Binding("r", "mark_read", "Mark Read", show=False),
        Binding("R", "mark_unread", "Mark Unread", show=False),
        Binding("shift+r", "mark_unread", "Mark Unread", show=False),
        Binding("d", "delete_chat", "Delete", show=False),
        Binding("u", "undo_action", "Undo", show=False),
        Binding("f2", "next_account", "Next Account", show=False),
        Binding("shift+f2", "prev_account", "Prev Account", show=False),
        Binding("?", "show_help", "Help", show=False),
        Binding("f1", "show_help", "Help", show=False),
        Binding("ctrl+k", "show_palette", "Palette", show=False),
        Binding("escape", "handle_escape", "Back/Clear", show=False),
    ]

    def __init__(self, gateway: Gateway, account: Account, **kwargs) -> None:
        super().__init__(**kwargs)
        self.gateway = gateway
        self.account = account
        self.accounts: list[Account] = []
        self.folders: list[Folder] = []
        self.current_folder_id: int | None = None
        self.filter_query: str = ""

        # Pending undo state: (description, undo_callback, expiry_timestamp)
        self._pending_undo: tuple[str, Callable[[], None], float] | None = None
        self._pending_confirm: tuple[str, Callable[[], None]] | None = None

        self.account_strip = AccountStrip()
        self.folder_strip = FolderStrip()
        self.status_bar = StatusBar()
        self.footer_bar = FooterBar(context="chats")
        self.filter_input = Input(placeholder="Filter chats...", id="filter-input")

        self.chat_list = WindowedList[Chat](
            items=[],
            renderer=self._render_row,
            id_fn=lambda c: c.chat_id,
            is_selectable_fn=lambda c: True,
            id="chat-window-list",
            enable_digit_motion=False,
        )

    def compose(self) -> ComposeResult:
        with Vertical():
            yield self.account_strip
            yield self.folder_strip
            with Container(id="filter-container"):
                yield self.filter_input
            with Container(id="chat-list-container"):
                yield self.chat_list
            yield self.status_bar
            yield self.footer_bar

    def on_mount(self) -> None:
        self._update_account_strip()
        self.folders = self.gateway.get_folders(self.account.user_id)
        self.folder_strip.set_folders(self.folders, active_folder_id=self.current_folder_id)

        self.refresh_chats(initial=True)
        self.chat_list.focus()
        self.set_interval(1.0, self._tick_undo)

        if hasattr(self.gateway, "register_listener"):
            self.gateway.register_listener(self._on_gateway_event)

        from tg_cli.config import has_seen_help
        if not has_seen_help():
            self.status_bar.set_status("Press ? for keys")

    def on_unmount(self) -> None:
        if hasattr(self.gateway, "unregister_listener"):
            self.gateway.unregister_listener(self._on_gateway_event)

    def _on_gateway_event(self, event_type: str, data: dict[str, Any]) -> None:
        def handle():
            if event_type == "chats_changed":
                if data.get("user_id") == self.account.user_id:
                    self.refresh_chats(initial=False)
            elif event_type == "folders_changed":
                if data.get("user_id") == self.account.user_id:
                    self.folders = self.gateway.get_folders(self.account.user_id)
                    self.folder_strip.set_folders(self.folders, active_folder_id=self.current_folder_id)
            elif event_type == "status":
                msg = data.get("message", "")
                is_err = data.get("is_error", False)
                self.status_bar.set_status(msg, is_error=is_err)

        import threading

        try:
            if threading.current_thread() is threading.main_thread():
                handle()
            else:
                self.app.call_from_thread(handle)
        except Exception:
            pass

    def _render_row(
        self, chat: Chat, width: int, is_cursor: bool, is_selected: bool
    ) -> Text:
        is_archive_header = (chat.chat_id == ARCHIVE_CHAT_ID)
        return render_chat_row(
            chat=chat,
            width=width,
            is_cursor=is_cursor,
            is_selected=is_selected,
            is_archive_header=is_archive_header,
            has_selection=bool(self.chat_list.selected_ids),
        )

    def refresh_chats(self, initial: bool = False) -> None:
        raw_chats = self.gateway.get_chats(
            account_id=self.account.user_id,
            folder_id=self.current_folder_id,
            filter_query=self.filter_query,
        )

        # Prepend fake Archive row at the top (unless we are viewing the archive folder)
        items: list[Chat] = []
        if self.current_folder_id != -2 and not self.filter_query:
            archive_row = Chat(
                account_id=self.account.user_id,
                chat_id=ARCHIVE_CHAT_ID,
                title="[Archive]",
                last_preview="",
            )
            items.append(archive_row)

        for c in raw_chats:
            draft = self.gateway.get_draft(self.account.user_id, c.chat_id)
            if draft and draft.text.strip():
                c.last_preview = f"Draft: {draft.text}"

        items.extend(raw_chats)
        self.chat_list.set_items(items, keep_cursor=not initial)

        if initial:
            # Cursor's first stop is the first real chat (index 1 if archive exists)
            if len(items) > 1:
                self.chat_list.cursor = 1
                self.chat_list._ensure_cursor_visible()
            else:
                self.chat_list.cursor = 0

        self.status_bar.set_status(f"{len(raw_chats)} chats loaded.")

    def on_windowed_list_item_activated(self, event: WindowedList.ItemActivated) -> None:
        self._open_chat(event.item)

    def on_windowed_list_text_copied(self, event: WindowedList.TextCopied) -> None:
        self.status_bar.set_status("Copied.")

    def _open_chat(self, chat: Chat) -> None:
        if chat.chat_id == ARCHIVE_CHAT_ID:
            # Switch to archived folder view
            self.current_folder_id = -2
            self.folder_strip.set_folders(self.folders, active_folder_id=-2)
            self.refresh_chats(initial=True)
            return

        if getattr(chat, "unavailable", False):
            self.status_bar.set_status(f"Chat '{chat.title}' is unavailable.", is_error=True)
            return

        from tg_cli.ui.screens.messages import MessagesScreen
        try:
            self.app.push_screen(
                MessagesScreen(gateway=self.gateway, account=self.account, chat=chat),
                callback=lambda _: self.refresh_chats(initial=False),
            )
        except Exception as e:
            self.status_bar.set_status(f"Cannot open chat: {e}", is_error=True)

    def action_open_focused(self) -> None:
        focused = self.chat_list.get_focused_item()
        if focused is not None:
            self._open_chat(focused)

    def action_start_filter(self) -> None:
        container = self.query_one("#filter-container")
        container.display = True
        self.filter_input.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self.filter_query = event.value
            self.chat_list.clear_selection()
            self.refresh_chats(initial=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter-input":
            # Return focus to list
            self.chat_list.focus()

    def action_prev_folder(self) -> None:
        all_folders = [None] + [f.folder_id for f in self.folders]
        try:
            curr_idx = all_folders.index(self.current_folder_id)
        except ValueError:
            curr_idx = 0
        new_idx = (curr_idx - 1) % len(all_folders)
        self.current_folder_id = all_folders[new_idx]
        self.folder_strip.set_folders(self.folders, active_folder_id=self.current_folder_id)
        self.chat_list.clear_selection()
        self.refresh_chats(initial=True)

    def action_next_folder(self) -> None:
        all_folders = [None] + [f.folder_id for f in self.folders]
        try:
            curr_idx = all_folders.index(self.current_folder_id)
        except ValueError:
            curr_idx = 0
        new_idx = (curr_idx + 1) % len(all_folders)
        self.current_folder_id = all_folders[new_idx]
        self.folder_strip.set_folders(self.folders, active_folder_id=self.current_folder_id)
        self.chat_list.clear_selection()
        self.refresh_chats(initial=True)

    def _update_account_strip(self) -> None:
        self.accounts = self.gateway.get_accounts()
        acc_idx = next(
            (i for i, a in enumerate(self.accounts) if a.user_id == self.account.user_id),
            0,
        )
        unread_counts = {}
        if hasattr(self.gateway, "get_account_unread_counts"):
            try:
                unread_counts = self.gateway.get_account_unread_counts()
            except Exception:
                pass

        from tg_cli.config import get_proxy_config, probe_proxy_latency
        proxy_cfg = get_proxy_config()
        proxy_str = None
        if proxy_cfg.get("enabled"):
            lat = probe_proxy_latency(proxy_cfg)
            proxy_str = f"[proxy: {lat}ms]" if lat is not None else "[proxy: ON]"

        self.account_strip.set_accounts(
            self.accounts,
            active_index=acc_idx,
            unread_counts=unread_counts,
            proxy_status=proxy_str,
        )

    def action_switch_account_index(self, index: int) -> None:
        if 0 <= index < len(self.accounts):
            self.account = self.accounts[index]
            self._update_account_strip()
            self.folders = self.gateway.get_folders(self.account.user_id)
            self.current_folder_id = None
            self.folder_strip.set_folders(self.folders, active_folder_id=None)
            self.chat_list.clear_selection()
            self.refresh_chats(initial=True)

    def action_next_account(self) -> None:
        if len(self.accounts) <= 1:
            return
        curr_idx = next(
            (i for i, a in enumerate(self.accounts) if a.user_id == self.account.user_id),
            0,
        )
        next_idx = (curr_idx + 1) % len(self.accounts)
        self.action_switch_account_index(next_idx)

    def action_prev_account(self) -> None:
        if len(self.accounts) <= 1:
            return
        curr_idx = next(
            (i for i, a in enumerate(self.accounts) if a.user_id == self.account.user_id),
            0,
        )
        prev_idx = (curr_idx - 1) % len(self.accounts)
        self.action_switch_account_index(prev_idx)

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
        self._update_account_strip()

    def action_manage_sessions(self) -> None:
        from tg_cli.ui.screens.sessions import SessionsScreen
        self.app.push_screen(SessionsScreen(gateway=self.gateway, account=self.account))

    def action_manage_profile(self) -> None:
        from tg_cli.ui.screens.profile import ProfileScreen
        self.app.push_screen(ProfileScreen(gateway=self.gateway, account=self.account))

    def _get_target_chats(self) -> list[Chat]:
        selected = self.chat_list.get_selected_items()
        if selected:
            return [c for c in selected if c.chat_id != ARCHIVE_CHAT_ID]
        focused = self.chat_list.get_focused_item()
        if focused and focused.chat_id != ARCHIVE_CHAT_ID:
            return [focused]
        return []

    def action_toggle_archive(self) -> None:
        targets = self._get_target_chats()
        if not targets:
            return
        target_ids = [c.chat_id for c in targets]
        old_archived = {c.chat_id: c.archived for c in targets}
        new_state = not targets[0].archived

        self.gateway.archive_chats(self.account.user_id, target_ids, archived=new_state)
        self.chat_list.clear_selection()
        self.refresh_chats()

        def do_undo() -> None:
            for cid, was_arch in old_archived.items():
                self.gateway.archive_chats(self.account.user_id, [cid], archived=was_arch)
            self.refresh_chats()

        state_label = "archived" if new_state else "unarchived"
        self._set_undoable(f"{len(targets)} chat(s) {state_label}", do_undo)

    def action_toggle_mute(self) -> None:
        targets = self._get_target_chats()
        if not targets:
            return
        target_ids = [c.chat_id for c in targets]
        old_muted = {c.chat_id: c.muted for c in targets}
        new_state = not targets[0].muted

        self.gateway.mute_chats(self.account.user_id, target_ids, muted=new_state)
        self.chat_list.clear_selection()
        self.refresh_chats()

        def do_undo() -> None:
            for cid, was_muted in old_muted.items():
                self.gateway.mute_chats(self.account.user_id, [cid], muted=was_muted)
            self.refresh_chats()

        state_label = "muted" if new_state else "unmuted"
        self._set_undoable(f"{len(targets)} chat(s) {state_label}", do_undo)

    def action_mark_read(self) -> None:
        targets = self._get_target_chats()
        if not targets:
            return
        target_ids = [c.chat_id for c in targets]
        old_unreads = {c.chat_id: c.unread_count for c in targets}

        self.gateway.mark_chats_read(self.account.user_id, target_ids, read=True)
        self.chat_list.clear_selection()
        self.refresh_chats()

        def do_undo() -> None:
            for cid, count in old_unreads.items():
                self.gateway.mark_chats_read(self.account.user_id, [cid], read=(count == 0))
            self.refresh_chats()

        self._set_undoable(f"Marked {len(targets)} chat(s) as read", do_undo)

    def action_mark_unread(self) -> None:
        targets = self._get_target_chats()
        if not targets:
            return
        target_ids = [c.chat_id for c in targets]
        old_unreads = {c.chat_id: c.unread_count for c in targets}

        self.gateway.mark_chats_read(self.account.user_id, target_ids, read=False)
        self.chat_list.clear_selection()
        self.refresh_chats()

        def do_undo() -> None:
            for cid, count in old_unreads.items():
                self.gateway.mark_chats_read(self.account.user_id, [cid], read=(count == 0))
            self.refresh_chats()

        self._set_undoable(f"Marked {len(targets)} chat(s) as unread", do_undo)

    def action_delete_chat(self) -> None:
        targets = self._get_target_chats()
        if not targets:
            return

        target_ids = [c.chat_id for c in targets]
        count = len(target_ids)

        def do_delete() -> None:
            # Store old chats for undo
            old_chats = [
                self.gateway.get_chat(self.account.user_id, cid) for cid in target_ids
            ]
            valid_old_chats = [c for c in old_chats if c is not None]
            self.gateway.delete_chats(self.account.user_id, target_ids)
            self.chat_list.clear_selection()
            self.refresh_chats()

            def do_undo() -> None:
                self.gateway.restore_chats(self.account.user_id, valid_old_chats)
                self.refresh_chats()

            self._set_undoable(f"Deleted {count} chat(s)", do_undo)

        # If more than 10 chats, ask y/n confirmation first
        if count > 10:
            self._request_confirm(
                f"Delete {count} chats for me? (y/n)",
                do_delete,
            )
        else:
            do_delete()

    def _set_undoable(self, description: str, undo_fn: Callable[[], None]) -> None:
        expiry = time.time() + 5.0
        self._pending_undo = (description, undo_fn, expiry)
        self.status_bar.set_toast(f"{description}. Press 'u' to undo (5s)")

    def action_undo_action(self) -> None:
        if self._pending_undo is not None:
            desc, undo_fn, _ = self._pending_undo
            self._pending_undo = None
            undo_fn()
            self.status_bar.set_toast(f"Undone: {desc}")
            self.status_bar.refresh()

    def _tick_undo(self) -> None:
        if self._pending_undo is not None:
            desc, _, expiry = self._pending_undo
            remaining = int(expiry - time.time())
            if remaining <= 0:
                self._pending_undo = None
                self.status_bar.set_toast(None)
            else:
                self.status_bar.set_toast(f"{desc}. Press 'u' to undo ({remaining}s)")

    def _request_confirm(self, prompt: str, on_confirm: Callable[[], None]) -> None:
        self._pending_confirm = (prompt, on_confirm)
        self.status_bar.set_prompt(prompt)

    def on_windowed_list_confirmation_prompt(
        self, event: WindowedList.ConfirmationPrompt
    ) -> None:
        self._request_confirm(event.prompt, event.on_confirm)

    def on_key(self, event: Key) -> None:
        # Handle pending confirmation prompt inline
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
                self.status_bar.set_status("Action cancelled.")
                event.prevent_default()
                event.stop()
                return

        container = self.query_one("#filter-container")
        if not container.display:
            key_char = event.character or event.key
            if key_char and key_char in "123456789":
                idx = int(key_char) - 1
                if 0 <= idx < len(self.accounts):
                    self.action_switch_account_index(idx)
                    event.prevent_default()
                    event.stop()
                    return

    def action_handle_escape(self) -> None:
        # Esc stack rule:
        # 1. Confirmation prompt
        if self._pending_confirm is not None:
            self._pending_confirm = None
            self.status_bar.set_prompt(None)
            return

        # 2. Filter active
        container = self.query_one("#filter-container")
        if container.display:
            container.display = False
            self.filter_query = ""
            self.filter_input.value = ""
            self.refresh_chats()
            self.chat_list.focus()
            return

        # 3. Selection active
        if self.chat_list.clear_selection():
            return

        # 4. Dragged text
        if self.chat_list.clear_text_selection():
            return

        # 5. Folder archive view -> return to All folders
        if self.current_folder_id == -2:
            self.current_folder_id = None
            self.folder_strip.set_folders(self.folders, active_folder_id=None)
            self.refresh_chats(initial=True)
            return

        # 6. Return to accounts screen
        self.app.pop_screen()

    def action_show_help(self) -> None:
        from tg_cli.ui.screens.help import HelpScreen
        self.app.push_screen(HelpScreen(context="chats"))

    def action_show_palette(self) -> None:
        from tg_cli.ui.screens.palette import CommandPalette
        self.app.push_screen(
            CommandPalette(context="chats"),
            callback=self._handle_palette_command,
        )

    def _handle_palette_command(self, cmd: Any) -> None:
        if cmd is None:
            return

        def execute() -> None:
            match cmd.id:
                case "open_chat":
                    self.action_open_focused()
                case "archive_chat":
                    self.action_toggle_archive()
                case "mute_chat":
                    self.action_toggle_mute()
                case "mark_read":
                    self.action_mark_read()
                case "mark_unread":
                    self.action_mark_unread()
                case "delete_chat":
                    self.action_delete_chat()
                case "undo":
                    self.action_undo_action()
                case "filter":
                    self.action_start_filter()
                case "account_next":
                    self.action_next_account()
                case "account_prev":
                    self.action_prev_account()
                case "toggle_proxy":
                    self.action_toggle_proxy()
                case "manage_sessions":
                    self.action_manage_sessions()
                case "manage_profile":
                    self.action_manage_profile()
                case "folder_prev":
                    self.action_prev_folder()
                case "folder_next":
                    self.action_next_folder()
                case "help":
                    self.action_show_help()
                case "select_all":
                    self.chat_list.action_select_all()
                case "toggle_select":
                    self.chat_list.action_toggle_select()
                case "clear_selection":
                    self.chat_list.clear_selection()
                case _ if getattr(cmd, "id", "").startswith("account_switch_"):
                    idx = int(cmd.id.split("_")[-1]) - 1
                    self.action_switch_account_index(idx)

        if getattr(cmd, "destructive", False):
            self._request_confirm(
                f"Run '{cmd.title}'? (y/n)",
                execute,
            )
        else:
            execute()
