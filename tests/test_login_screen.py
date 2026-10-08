"""Tests for LoginScreen and account addition/removal."""

import pytest
from textual.app import App, ComposeResult
from tg_cli.domain.models import Account, AuthState
from tg_cli.telegram.fake import FakeGateway
from tg_cli.ui.screens.accounts import AccountsScreen
from tg_cli.ui.screens.login import LoginScreen


class MockLoginGateway(FakeGateway):
    """FakeGateway with hooks for testing login flow."""

    def __init__(self) -> None:
        super().__init__(chat_count=10)
        self.code_requested = False
        self.code_submitted = ""
        self.password_submitted = ""

    def start_login(self, phone: str, api_id: int, api_hash: str) -> None:
        self.code_requested = True

    def submit_login_code(self, code: str) -> None:
        self.code_submitted = code

    def submit_login_password(self, password: str) -> None:
        self.password_submitted = password


class LoginTestApp(App):
    def __init__(self, gateway) -> None:
        super().__init__()
        self.gateway = gateway
        self.login_result: bool | None = None

    def on_mount(self) -> None:
        self.push_screen(AccountsScreen(gateway=self.gateway))


@pytest.mark.asyncio
async def test_login_flow_steps():
    gw = MockLoginGateway()
    app = LoginTestApp(gateway=gw)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, AccountsScreen)

        # Press 'A' to open LoginScreen
        await pilot.press("A")
        await pilot.pause()
        assert isinstance(app.screen, LoginScreen)
        login_screen: LoginScreen = app.screen

        # If credentials needed:
        if login_screen.step == "credentials":
            login_screen.input_widget.value = "123456:abcdef123456"
            await pilot.press("enter")
            await pilot.pause()

        assert login_screen.step == "phone"

        # Submit phone
        login_screen.input_widget.value = "+12025550199"
        await pilot.press("enter")
        await pilot.pause()
        assert gw.code_requested is True

        # Simulate code sent
        login_screen.on_login_code_sent("+12025550199")
        assert login_screen.step == "code"

        # Simulate invalid code error
        login_screen.on_login_error("Invalid code. Please try again.")
        assert "Invalid code" in login_screen.error_message
        # Still on code step!
        assert login_screen.step == "code"

        # Simulate 2FA required
        login_screen.on_login_2fa_needed("Enter password")
        assert login_screen.step == "password"

        # Simulate login success
        # Add account to gateway and dismiss
        new_acc = Account(
            user_id=2001,
            phone="+12025550199",
            username="newuser",
            display_name="New User",
            auth_state=AuthState.OK,
        )
        gw.add_account(new_acc)
        login_screen.on_login_success()
        await pilot.pause()

        # Should be back on AccountsScreen
        assert isinstance(app.screen, AccountsScreen)
        accounts_screen: AccountsScreen = app.screen
        assert any(a.user_id == 2001 for a in accounts_screen.accounts_list.items)


@pytest.mark.asyncio
async def test_account_removal_confirmation():
    gw = MockLoginGateway()
    initial_count = len(gw.get_accounts())
    app = LoginTestApp(gateway=gw)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, AccountsScreen)
        acc_screen: AccountsScreen = app.screen

        # Press 'D' to remove account
        await pilot.press("D")
        await pilot.pause()
        assert acc_screen._pending_confirm is not None
        assert "Remove account" in acc_screen.status_bar.prompt_message

        # Press 'y' to confirm removal
        await pilot.press("y")
        await pilot.pause()
        assert acc_screen._pending_confirm is None
        assert len(gw.get_accounts()) == initial_count - 1


def test_telethon_gateway_listener_dispatch(tmp_path):
    from tg_cli.telegram.telethon_gw import TelethonGateway
    db_file = tmp_path / "test.db"
    events_received = []

    def dummy_listener(evt, data):
        events_received.append((evt, data))

    gw = TelethonGateway(db_path=db_file)
    gw.register_listener(dummy_listener)

    # Trigger worker event dispatch
    gw._on_worker_event("login_code_sent", {"phone": "+123456789"})
    assert len(events_received) == 1
    assert events_received[0] == ("login_code_sent", {"phone": "+123456789"})

    gw.unregister_listener(dummy_listener)
    gw._on_worker_event("qr_login_token", {"url": "tg://login?token=abc", "expires_in": 30})
    assert len(events_received) == 1
    gw.conn.close()


@pytest.mark.asyncio
async def test_login_screen_gateway_event_transition():
    gw = FakeGateway(chat_count=1)
    screen = LoginScreen(gateway=gw, start_mode="phone")
    app = App()

    async with app.run_test():
        await app.push_screen(screen)
        assert screen.step == "phone"

        # Dispatch login_code_sent via _on_gateway_event
        screen._on_gateway_event("login_code_sent", {"phone": "+123456789"})
        assert screen.step == "code"

        # Dispatch 2FA needed
        screen._on_gateway_event("login_2fa_needed", {"hint": "test hint"})
        assert screen.step == "password"


@pytest.mark.asyncio
async def test_phone_input_border_is_box_drawing():
    """The phone field must not use Textual's `tall` eighth-block border.

    Those glyphs (U+258A ▊, U+2594 ▔, U+258E ▎, U+2581 ▁) are missing in
    common Windows console fonts and show up as a box of question marks.
    """
    gw = FakeGateway(chat_count=1)
    app = App()

    async with app.run_test(size=(90, 28)) as pilot:
        screen = LoginScreen(gateway=gw, start_mode="phone")
        await app.push_screen(screen)
        await pilot.pause()
        assert screen.step == "phone"

        border = screen.query_one("#login-input").styles.border
        assert border.top[0] == "solid"
        assert border.bottom[0] == "solid"

        rendered = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
        assert "┌" in rendered and "─" in rendered and "┐" in rendered
        assert "+12025550101" in rendered
        for missing in ("\u258a", "\u2594", "\u258e", "\u2581"):
            assert missing not in rendered

