"""Unread count in the terminal tab title."""

from __future__ import annotations

import pytest
from tg_cli.app import TelegramCLIApp, terminal_title
from tg_cli.telegram.fake import FakeGateway


def test_title_puts_the_unread_count_first():
    assert terminal_title(0) == "tg-cli"
    assert terminal_title(4) == "(4) tg-cli"


@pytest.mark.asyncio
async def test_title_follows_unread_and_is_written_only_on_change(monkeypatch):
    written: list[str] = []
    monkeypatch.setattr(TelegramCLIApp, "_write_terminal_title", lambda self, t: written.append(t))
    gw = FakeGateway(chat_count=5)
    acc = gw.get_accounts()[0]
    for chat in gw.chats[acc.user_id].values():
        chat.unread_count = 0
        chat.muted = False
    app = TelegramCLIApp(gateway=gw)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert written[-1] == "tg-cli"

        chats = list(gw.chats[acc.user_id].values())
        chats[0].unread_count = 3
        chats[1].unread_count = 2
        app.update_terminal_title()
        assert written[-1] == "(5) tg-cli"
        before = len(written)
        app.update_terminal_title()
        assert len(written) == before

        # Muted chats do not count, like the account strip.
        chats[1].muted = True
        app.update_terminal_title()
        assert written[-1] == "(3) tg-cli"
