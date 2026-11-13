"""Login code delivery: Telegram accepting sendCode is not an SMS."""

import pytest
from textual.app import App
from telethon.tl.types.auth import (
    CodeTypeSms,
    SentCode,
    SentCodePaymentRequired,
    SentCodeTypeApp,
    SentCodeTypeFirebaseSms,
    SentCodeTypeSms,
)
from tg_cli.telegram.fake import FakeGateway
from tg_cli.domain.models import Account
from tg_cli.telegram.worker import (
    describe_sent_code,
    extract_login_code,
    login_code_needs_followup,
    read_session_dc,
)
from tg_cli.ui.screens.login import LoginScreen


def test_app_code_is_inside_telegram_not_sms():
    sent = SentCode(
        type=SentCodeTypeApp(length=5),
        phone_code_hash="secret-hash",
        next_type=CodeTypeSms(),
        timeout=60,
    )
    info = describe_sent_code(sent, "+12025550199")
    assert info["delivery"] == "app"
    assert info["delivered"] is True
    assert info["resend_in"] == 60
    assert "number you typed" in info["hint"]
    assert "second account" in info["hint"]
    assert "secret-hash" not in info["hint"]
    assert "phone_code_hash" not in info
    assert login_code_needs_followup("app", 60) is True
    assert login_code_needs_followup("app", 0) is False
    assert login_code_needs_followup("firebase", 0) is True
    assert login_code_needs_followup("sms", 0) is False


def test_app_code_without_a_fallback_is_left_alone():
    sent = SentCode(
        type=SentCodeTypeApp(length=5),
        phone_code_hash="secret-hash",
    )
    info = describe_sent_code(sent, "+998901234567")
    assert info["delivery"] == "app"
    assert info["delivered"] is False
    assert info["resend_in"] == 0
    assert info["can_resend"] is False
    assert info["next"] == ""
    assert "did not send an SMS" in info["hint"]
    assert "Scan with the phone logged into this number" in info["hint"]
    assert "secret-hash" not in info["hint"]
    assert login_code_needs_followup(info["delivery"], info["resend_in"]) is False


def test_extract_login_code_reads_the_service_message():
    assert extract_login_code("Login code: 48291. Do not give this code to anyone.") == "48291"
    assert extract_login_code("no digits here") == ""
    assert extract_login_code("id 1234567890") == ""


def test_read_session_dc_reads_the_session_file(tmp_path, monkeypatch):
    session = tmp_path / "42.session"
    import sqlite3
    conn = sqlite3.connect(session)
    conn.execute("create table sessions (dc_id int, server_address text, port int, auth_key blob)")
    conn.execute("insert into sessions values (4, '149.154.167.91', 443, null)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        "tg_cli.telegram.worker.get_session_path",
        lambda user_id: tmp_path / f"{user_id}.session",
    )
    assert read_session_dc(42) == 4
    assert read_session_dc(99) is None


def test_firebase_sms_has_not_sent_a_code():
    sent = SentCode(
        type=SentCodeTypeFirebaseSms(length=5),
        phone_code_hash="secret-hash",
        next_type=CodeTypeSms(),
        timeout=30,
    )
    info = describe_sent_code(sent, "+998901234567")
    assert info["delivery"] == "firebase"
    assert info["delivered"] is False
    assert info["resend_in"] == 30
    assert "did not send a code" in info["hint"]
    assert "secret-hash" not in info["hint"]


def test_sms_code_names_the_phone():
    sent = SentCode(
        type=SentCodeTypeSms(length=5),
        phone_code_hash="secret-hash",
    )
    info = describe_sent_code(sent, "+12025550199")
    assert info["delivery"] == "sms"
    assert info["delivered"] is True
    assert "SMS to +12025550199" in info["hint"]


def test_payment_required_sends_nothing():
    sent = SentCodePaymentRequired(
        store_product="login",
        phone_code_hash="secret-hash",
        support_email_address="a@b.c",
        support_email_subject="pay",
        currency="USD",
        amount=100,
    )
    info = describe_sent_code(sent, "+12025550199")
    assert info["delivery"] == "payment"
    assert info["delivered"] is False
    assert "secret-hash" not in info["hint"]


class _OneAccount:
    def __init__(self, phone: str) -> None:
        self.accounts = [Account(user_id=7, phone=phone)]

    def get_accounts(self):
        return list(self.accounts)


@pytest.mark.asyncio
async def test_phone_step_leaves_room_for_a_second_number():
    gw = _OneAccount("+15551230039")
    app = App()
    async with app.run_test(size=(90, 32)) as pilot:
        screen = LoginScreen(gateway=gw, start_mode="phone")
        await app.push_screen(screen)
        await pilot.pause()
        assert screen.input_widget.value == ""
        assert "+15551230039" not in "\n".join(
            strip.text for strip in app.screen._compositor.render_strips()
        )
        screen.on_login_code_sent("+15551230039", hint="waiting", delivered=True, resend_in=0)
        screen._on_gateway_event("login_code_visible", {"code": "48291"})
        await pilot.pause()
        assert screen.input_widget.value == "48291"
        rendered = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
        assert "Press Enter" in rendered


class _ResendGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__(chat_count=1)
        self.resend_calls = 0

    def resend_login_code(self) -> None:
        self.resend_calls += 1


@pytest.mark.asyncio
async def test_code_screen_shows_real_destination_and_resend():
    gw = _ResendGateway()
    app = App()
    async with app.run_test(size=(90, 32)) as pilot:
        screen = LoginScreen(gateway=gw, start_mode="phone")
        await app.push_screen(screen)
        await pilot.pause()
        screen._on_gateway_event(
            "login_code_sent",
            {
                "phone": "+12025550199",
                "hint": "The code was sent inside Telegram, not by SMS.",
                "delivered": True,
                "resend_in": 0,
            },
        )
        await pilot.pause()
        assert screen.step == "code"
        rendered = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
        assert "not by SMS" in rendered
        assert "via Telegram/SMS" not in rendered

        await pilot.press("ctrl+r")
        await pilot.pause()
        assert gw.resend_calls == 1

        await pilot.press("ctrl+r")
        await pilot.pause()
        assert gw.resend_calls == 1


@pytest.mark.asyncio
async def test_resend_waits_for_telegram_timeout():
    gw = _ResendGateway()
    app = App()
    async with app.run_test(size=(90, 32)) as pilot:
        screen = LoginScreen(gateway=gw, start_mode="code")
        screen.phone = "+12025550199"
        await app.push_screen(screen)
        await pilot.pause()
        screen.on_login_code_sent("+12025550199", hint="waiting", delivered=False, resend_in=25)
        await pilot.pause()
        rendered = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
        assert "No code was sent yet" in rendered
        assert "another method in 25s" in rendered

        await pilot.press("ctrl+r")
        await pilot.pause()
        assert gw.resend_calls == 0
        rendered = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
        assert "will not send another code" in rendered


@pytest.mark.asyncio
async def test_app_without_fallback_does_not_ask_telegram_again():
    gw = _ResendGateway()
    app = App()
    async with app.run_test(size=(100, 32)) as pilot:
        screen = LoginScreen(gateway=gw, start_mode="phone")
        await app.push_screen(screen)
        await pilot.pause()
        sent = SentCode(type=SentCodeTypeApp(length=5), phone_code_hash="secret-hash")
        info = describe_sent_code(sent, "+998901234563")
        screen._on_gateway_event("login_code_sent", info)
        await pilot.pause()
        rendered = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
        assert "did not send an SMS" in rendered
        assert "No code will arrive on the phone" in rendered
        assert "secret-hash" not in rendered

        await pilot.press("ctrl+r")
        await pilot.pause()
        assert gw.resend_calls == 0
        rendered = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
        assert "no other way" in rendered
