"""Links in messages: which are found, which are safe, and `o` opening them."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon.tl.types import MessageEntityBold, MessageEntityTextUrl
from tg_cli.app import TelegramCLIApp
from tg_cli.domain.links import is_safe_link, message_links
from tg_cli.domain.models import Message
from tg_cli.telegram.fake import FakeGateway
from tg_cli.telegram.map import map_telethon_message_to_domain
from tg_cli.ui.screens.messages import MessagesScreen


def _msg(text: str = "", caption: str = "", spans=None) -> Message:
    return Message(
        account_id=1,
        chat_id=1,
        message_id=1,
        plain_text=text,
        caption=caption,
        entity_spans=spans or [],
    )


def test_bare_links_lose_the_punctuation_that_ends_the_sentence():
    msg = _msg("See https://example.com/a. Or (www.example.org/b), ok?")
    assert message_links(msg) == ["https://example.com/a", "https://www.example.org/b"]


def test_a_link_keeps_its_own_closing_parenthesis():
    msg = _msg("https://en.wikipedia.org/wiki/Python_(programming_language).")
    assert message_links(msg) == ["https://en.wikipedia.org/wiki/Python_(programming_language)"]


def test_hidden_text_links_come_first_and_unsafe_ones_are_dropped():
    msg = _msg(
        "click here and https://a.example",
        spans=[
            {"type": "text_url", "url": "https://hidden.example"},
            {"type": "text_url", "url": "file:///C:/Windows/System32/calc.exe"},
            {"type": "text_url", "url": "https://a.example"},
        ],
    )
    assert message_links(msg) == ["https://hidden.example", "https://a.example"]


def test_only_web_telegram_and_mail_links_are_safe():
    assert is_safe_link("https://x.example")
    assert is_safe_link("tg://resolve?domain=durov")
    assert is_safe_link("mailto:a@example.com")
    assert not is_safe_link("file:///C:/x.exe")
    assert not is_safe_link("C:\\Windows\\notepad.exe")
    assert not is_safe_link("javascript:alert(1)")


def test_map_keeps_the_target_of_a_text_link():
    raw = SimpleNamespace(
        id=5,
        message="read the docs",
        date=datetime.now(timezone.utc),
        sender_id=1,
        sender=None,
        out=False,
        media=None,
        reply_to=None,
        fwd_from=None,
        edit_date=None,
        entities=[
            MessageEntityBold(offset=0, length=4),
            MessageEntityTextUrl(offset=9, length=4, url="https://docs.example"),
        ],
    )
    msg = map_telethon_message_to_domain(raw, account_id=1, chat_id=2)
    assert msg.entity_spans == [
        {"type": "text_url", "offset": 9, "length": 4, "url": "https://docs.example"}
    ]


@pytest.mark.asyncio
async def test_o_opens_each_link_in_turn(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("tg_cli.ui.screens.messages.webbrowser.open", opened.append)
    gw = FakeGateway(chat_count=3)
    acc = gw.get_accounts()[0]
    chat = gw.get_chats(acc.user_id)[0]
    gw.messages[(acc.user_id, chat.chat_id)].append(
        Message(
            account_id=acc.user_id,
            chat_id=chat.chat_id,
            message_id=9999,
            sender_name="Bob",
            date=datetime.now(timezone.utc),
            plain_text="https://one.example and https://two.example",
        )
    )
    screen = MessagesScreen(gateway=gw, account=acc, chat=chat)
    app = TelegramCLIApp(gateway=gw)

    async with app.run_test() as pilot:
        await app.push_screen(screen)
        await pilot.pause()
        assert screen.message_list.get_focused_item().message_id == 9999
        await pilot.press("o")
        await pilot.press("o")
        await pilot.press("o")
        assert opened == ["https://one.example", "https://two.example", "https://one.example"]
        assert "1 of 2" in screen.status_bar.status_message
