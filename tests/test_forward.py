"""Forwarding: the chat picker and the Telegram call behind it."""

from __future__ import annotations

import time

import pytest
from tg_cli.app import TelegramCLIApp
from tg_cli.domain.models import Account, Chat, Message
from tg_cli.store.repo import get_messages, upsert_account, upsert_chats, upsert_messages
from tg_cli.telegram.telethon_gw import TelethonGateway
from tg_cli.ui.screens.forward import ForwardPicker
from tg_cli.ui.screens.messages import MessagesScreen

from tests.test_phase3_messages import MessagePagingGateway
from tests.test_worker import MockTelethonClient, MockTelethonMessage, _wait_until


async def _open_chat(app, gw):
    alice = gw.get_accounts()[0]
    screen = MessagesScreen(gateway=gw, account=alice, chat=gw.get_chat(alice.user_id, 1))
    await app.push_screen(screen)
    return alice, screen


@pytest.mark.asyncio
async def test_f_forwards_the_selection_to_the_picked_chat():
    gw = MessagePagingGateway()
    app = TelegramCLIApp(gateway=gw)
    async with app.run_test() as pilot:
        alice, screen = await _open_chat(app, gw)
        await pilot.pause()
        await pilot.press("shift+up", "f")
        await pilot.pause()
        assert isinstance(app.screen, ForwardPicker)
        assert "Forward 2 message(s)" in str(app.screen.query_one("#forward-title").render())

        await pilot.press("f", "a", "m")
        await pilot.pause()
        assert [c.title for c in app.screen.matches] == ["Family Group"]
        await pilot.press("enter")
        await pilot.pause()

        assert app.screen is screen
        assert gw.forward_calls == [(alice.user_id, 1, [119, 120], 4, False)]
        assert screen.message_list.selected_ids == set()
        assert gw.messages[(alice.user_id, 4)][-1].forward_label == "Alice"


@pytest.mark.asyncio
async def test_tab_hides_the_sender_and_esc_cancels():
    gw = MessagePagingGateway()
    app = TelegramCLIApp(gateway=gw)
    async with app.run_test() as pilot:
        alice, screen = await _open_chat(app, gw)
        await pilot.pause()

        await pilot.press("f")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen
        assert gw.forward_calls == []
        assert "cancelled" in screen.status_bar.status_message

        await pilot.press("f")
        await pilot.pause()
        await pilot.press("tab")
        assert app.screen.hide_sender is True
        await pilot.press("down", "enter")
        await pilot.pause()
        assert gw.forward_calls == [(alice.user_id, 1, [120], 2, True)]


def test_worker_forwards_and_stores_the_copies(tmp_path):
    gw = TelethonGateway(db_path=tmp_path / "gw_forward.db")
    gw.start()
    calls: list[dict] = []

    class ForwardingClient(MockTelethonClient):
        async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
            calls.append({"to": entity, "ids": messages, "from": from_peer, **kwargs})
            return [MockTelethonMessage(msg_id=900 + i, text="copy", out=True) for i, _ in enumerate(messages)]

    try:
        upsert_account(gw.conn, Account(user_id=777, phone="+12025550177", display_name="Mock User"))
        upsert_chats(gw.conn, [
            Chat(account_id=777, chat_id=40, title="From"),
            Chat(account_id=777, chat_id=41, title="To"),
        ])
        upsert_messages(gw.conn, [Message(account_id=777, chat_id=40, message_id=5, plain_text="x")])
        gw.worker.clients[777] = ForwardingClient()

        gw.forward_messages(777, 40, [5], 41, drop_author=True)
        assert _wait_until(lambda: [m.message_id for m in get_messages(gw.conn, 777, 41)] == [900])
        assert calls == [{"to": 41, "ids": [5], "from": 40, "drop_author": True}]
    finally:
        gw.stop()
