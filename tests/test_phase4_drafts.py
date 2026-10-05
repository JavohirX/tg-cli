"""Tests for Phase 4: Sending, replying, editing, drafts, and retrying failed messages."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from tg_cli.domain.models import Account, Chat, ChatKind, Draft, Message, MessageKind, SendState
from tg_cli.domain.render import render_chat_row
from tg_cli.telegram.fake import FakeGateway
from tg_cli.ui.screens.chats import ChatsScreen
from tg_cli.ui.screens.messages import MessagesScreen


class MessagesPilotApp(App):
    def __init__(self, gateway: FakeGateway, account: Account, chat: Chat) -> None:
        super().__init__()
        self.gateway = gateway
        self.account = account
        self.chat = chat

    def compose(self) -> ComposeResult:
        return []

    def on_mount(self) -> None:
        self.push_screen(
            MessagesScreen(gateway=self.gateway, account=self.account, chat=self.chat)
        )


@pytest.mark.asyncio
async def test_draft_saving_and_restoration():
    gw = FakeGateway(chat_count=5)
    acc = gw.get_accounts()[0]
    chat = gw.get_chats(acc.user_id)[0]

    # 1. Open MessagesScreen, type draft, unmount (pop screen)
    app = MessagesPilotApp(gw, acc, chat)
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, MessagesScreen)

        # Type draft in composer
        screen.composer.text = "Hello unsaved draft"
        # Pop screen to trigger unmount and draft saving
        app.pop_screen()
        await pilot.pause()

    # Verify draft was saved in gateway
    draft = gw.get_draft(acc.user_id, chat.chat_id)
    assert draft is not None
    assert draft.text == "Hello unsaved draft"

    # 2. Re-open MessagesScreen, verify draft text is restored
    app2 = MessagesPilotApp(gw, acc, chat)
    async with app2.run_test() as pilot:
        screen2 = app2.screen
        assert isinstance(screen2, MessagesScreen)
        assert screen2.composer.text == "Hello unsaved draft"

        # Submit message and check that draft is cleared
        screen2.composer.process_enter()
        await pilot.pause()

    # Draft should be cleared after sending
    draft_after = gw.get_draft(acc.user_id, chat.chat_id)
    assert draft_after is None


@pytest.mark.asyncio
async def test_draft_with_reply_context():
    gw = FakeGateway(chat_count=5)
    acc = gw.get_accounts()[0]
    chat = gw.get_chats(acc.user_id)[0]

    # Pre-save a draft with reply_to_id
    msgs = gw.get_messages(acc.user_id, chat.chat_id)
    target_msg = msgs[0]
    gw.save_draft(
        Draft(
            account_id=acc.user_id,
            chat_id=chat.chat_id,
            text="Replying later",
            reply_to_id=target_msg.message_id,
        )
    )

    app = MessagesPilotApp(gw, acc, chat)
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, MessagesScreen)
        assert screen.composer.text == "Replying later"
        assert screen.composer.reply_to_id == target_msg.message_id
        assert screen.composer.context_bar.display is True


@pytest.mark.asyncio
async def test_retry_failed_message_on_enter():
    gw = FakeGateway(chat_count=5)
    acc = gw.get_accounts()[0]
    chat = gw.get_chats(acc.user_id)[0]

    # Inject a failed message into history
    failed_msg = Message(
        account_id=acc.user_id,
        chat_id=chat.chat_id,
        message_id=99999,
        sender_id=acc.user_id,
        sender_name="Me",
        plain_text="Failed to send this",
        send_state=SendState.FAILED,
        outgoing=True,
    )
    gw.messages[(acc.user_id, chat.chat_id)].append(failed_msg)

    app = MessagesPilotApp(gw, acc, chat)
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, MessagesScreen)

        # Move cursor to the failed message (which is at the bottom)
        msg_items = [it for it in screen.message_list.items if isinstance(it, Message)]
        target_idx = next(i for i, it in enumerate(screen.message_list.items) if getattr(it, "message_id", None) == 99999)
        screen.message_list.cursor = target_idx

        # Press enter on message list to activate retry
        await pilot.press("enter")
        await pilot.pause()

        # Check that retry_failed_message updated send_state
        updated = next(m for m in gw.messages[(acc.user_id, chat.chat_id)] if m.message_id == 99999)
        assert updated.send_state == SendState.SENT


def test_chat_row_draft_rendering():
    chat = Chat(
        account_id=1,
        chat_id=10,
        title="Test Group",
        kind=ChatKind.GROUP,
        last_preview="Draft: Don't forget tomorrow's meeting",
    )
    rendered = render_chat_row(chat, width=80)
    plain = rendered.plain
    assert "Draft: Don't forget" in plain

    # Check that 'Draft: ' has bold red styling
    has_bold_red_draft = any(
        span.style == "bold red" for span in rendered.spans
    )
    assert has_bold_red_draft
