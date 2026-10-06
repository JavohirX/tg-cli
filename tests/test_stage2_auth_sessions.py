"""Comprehensive tests for Stage 2: Authentication, Accounts & Sessions.

Covers:
- Terminal ASCII QR code login generation (UTF-8 half-blocks).
- LoginScreen QR mode lifecycle, token countdown, and auto-refresh/scan.
- MTProto/SOCKS5/HTTP Proxy configuration, latency probing, and toggling.
- Global unread badges and proxy status on AccountStrip.
- Instant Account Switcher (keys 1-9, F2 / Shift+F2).
- Active Sessions Manager screen (inspect authorizations, revoke with y/n confirmation).
- User Profile & Status Management screen (view profile, inline edit bio and username).
"""

from __future__ import annotations

import socket
import pytest
from unittest.mock import MagicMock, patch

from tg_cli.app import TelegramCLIApp
from tg_cli.config import (
    DEFAULT_PROXY,
    get_proxy_config,
    probe_proxy_latency,
    save_proxy_config,
    toggle_proxy,
)
from tg_cli.domain.models import Account, AuthState, SessionInfo, UserProfile
from tg_cli.telegram.fake import FakeGateway
from tg_cli.ui.chrome import AccountStrip
from tg_cli.ui.qr import generate_qr_ascii, render_progress_bar
from tg_cli.ui.screens.accounts import AccountsScreen
from tg_cli.ui.screens.chats import ChatsScreen
from tg_cli.ui.screens.login import LoginScreen
from tg_cli.ui.screens.palette import CommandPalette
from tg_cli.ui.screens.profile import ProfileScreen
from tg_cli.ui.screens.sessions import SessionsScreen


# ---------------------------------------------------------------------------
# 1. QR Code Generator & Progress Bar Tests
# ---------------------------------------------------------------------------


def test_generate_qr_ascii():
    """Verify ASCII QR generation produces valid UTF-8 half-blocks."""
    url = "tg://login?token=test_qr_token_123456789"
    lines = generate_qr_ascii(url, border=1, invert=True)

    assert isinstance(lines, list)
    assert len(lines) > 5

    # Check all lines have equal length
    width = len(lines[0])
    assert width > 10
    for line in lines:
        assert len(line) == width

    # Check UTF-8 half-block characters are present
    full_text = "".join(lines)
    assert any(c in full_text for c in ("▄", "▀", "█", " "))


def test_generate_qr_ascii_options():
    """Verify border and inversion variations."""
    url = "https://t.me/test"
    lines_b1 = generate_qr_ascii(url, border=1)
    lines_b2 = generate_qr_ascii(url, border=2)
    assert len(lines_b2) > len(lines_b1)

    lines_inv_false = generate_qr_ascii(url, invert=False)
    assert len(lines_inv_false) == len(lines_b1)


def test_render_progress_bar():
    """Verify QR countdown progress bar formatting."""
    bar_full = render_progress_bar(30, 30, width=20)
    assert "100%" in bar_full
    assert "30s" in bar_full
    assert "█" in bar_full

    bar_half = render_progress_bar(15, 30, width=20)
    assert "50%" in bar_half
    assert "15s" in bar_half

    bar_empty = render_progress_bar(0, 30, width=20)
    assert "0%" in bar_empty
    assert "0s" in bar_empty


# ---------------------------------------------------------------------------
# 2. Proxy Configuration & Latency Probe Tests
# ---------------------------------------------------------------------------


def test_proxy_config_and_toggle(tmp_path, monkeypatch):
    """Verify proxy config read, write, and toggle behavior."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr("tg_cli.config.get_config_path", lambda: cfg_file)

    # Initial default config
    cfg = get_proxy_config()
    assert cfg["enabled"] is False
    assert cfg["type"] == "socks5"
    assert cfg["addr"] == "127.0.0.1"
    assert cfg["port"] == 1080

    # Toggle proxy ON
    is_enabled = toggle_proxy()
    assert is_enabled is True
    cfg_after = get_proxy_config()
    assert cfg_after["enabled"] is True

    # Toggle proxy OFF
    is_enabled_again = toggle_proxy()
    assert is_enabled_again is False
    cfg_final = get_proxy_config()
    assert cfg_final["enabled"] is False


def test_probe_proxy_latency():
    """Verify proxy latency probe with mock socket."""
    proxy_cfg = {"addr": "127.0.0.1", "port": 1080}

    # Simulate successful socket connection
    with patch("socket.create_connection") as mock_conn:
        mock_sock = MagicMock()
        mock_conn.return_value = mock_sock
        lat = probe_proxy_latency(proxy_cfg, timeout=1.0)
        assert lat is not None
        assert isinstance(lat, (int, float))
        assert lat >= 0
        mock_sock.__exit__.assert_called_once()

    # Simulate connection failure (e.g. refused or timeout)
    with patch("socket.create_connection", side_effect=OSError("Connection refused")):
        lat_err = probe_proxy_latency(proxy_cfg, timeout=1.0)
        assert lat_err is None


# ---------------------------------------------------------------------------
# 3. Fake Gateway Stage 2 Feature Tests
# ---------------------------------------------------------------------------


def test_fake_gateway_sessions_and_profile():
    gw = FakeGateway(chat_count=5)
    acc = gw.get_accounts()[0]

    # Test sessions
    sessions = gw.get_active_sessions(acc.user_id)
    assert len(sessions) >= 2
    assert any(s.is_current for s in sessions)

    other_session = next(s for s in sessions if not s.is_current)
    assert gw.revoke_session(acc.user_id, other_session.hash) is True

    sessions_after = gw.get_active_sessions(acc.user_id)
    assert len(sessions_after) == len(sessions) - 1
    assert all(s.hash != other_session.hash for s in sessions_after)

    # Cannot revoke current session
    curr_session = next(s for s in sessions_after if s.is_current)
    assert gw.revoke_session(acc.user_id, curr_session.hash) is False

    # Test profile
    prof = gw.get_user_profile(acc.user_id)
    assert prof.user_id == acc.user_id

    gw.update_user_profile(acc.user_id, bio="New terminal bio", username="updated_user")
    prof_updated = gw.get_user_profile(acc.user_id)
    assert prof_updated.bio == "New terminal bio"
    assert prof_updated.username == "updated_user"

    # Test account unread counts
    counts = gw.get_account_unread_counts()
    assert isinstance(counts, dict)
    assert acc.user_id in counts


def test_fake_gateway_qr_login_flow():
    gw = FakeGateway()
    events = []

    def listener(event_type: str, data: dict):
        events.append((event_type, data))

    gw.register_listener(listener)
    gw.start_qr_login(api_id=123, api_hash="hash")

    # Should dispatch qr_login_token event
    assert any(e[0] == "qr_login_token" for e in events)
    token_ev = next(e for e in events if e[0] == "qr_login_token")
    url = token_ev[1]["url"]

    # Simulate mobile scan
    gw.simulate_qr_scan(url)
    assert any(e[0] == "login_success" for e in events)


# ---------------------------------------------------------------------------
# 4. Account Strip UI Widget Tests
# ---------------------------------------------------------------------------


def test_account_strip_rendering():
    accounts = [
        Account(user_id=1, phone="+111", username="alice", display_name="Alice", auth_state=AuthState.OK),
        Account(user_id=2, phone="+222", username="work", display_name="Work", auth_state=AuthState.OK),
    ]
    unread = {1: 3, 2: 0}

    strip = AccountStrip()
    strip.set_accounts(accounts, active_index=0, unread_counts=unread, proxy_status="[proxy: 42ms]")

    rendered = strip.render()
    plain = rendered.plain
    assert "1: @alice (3)" in plain
    assert "2: @work" in plain
    assert "[proxy: 42ms]" in plain


# ---------------------------------------------------------------------------
# 5. LoginScreen Pilot Tests: QR Code Flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_screen_qr_flow():
    gw = FakeGateway()
    # Mock config with credentials to skip credentials step
    with patch("tg_cli.ui.screens.login.load_config", return_value={"api_id": 12345, "api_hash": "abcdef"}):
        app = TelegramCLIApp(gateway=gw)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, AccountsScreen)

            # Press 'Q' to open LoginScreen directly in QR mode
            await pilot.press("Q")
            await pilot.pause()
            assert isinstance(app.screen, LoginScreen)
            login_screen: LoginScreen = app.screen

            assert login_screen.step == "qr"
            assert login_screen.qr_widget.display is True
            assert login_screen.progress_widget.display is True
            assert login_screen.input_widget.display is False

            # Switch to phone mode by pressing 'p'
            await pilot.press("p")
            await pilot.pause()
            assert login_screen.step == "phone"
            assert login_screen.input_widget.display is True

            # Switch back to QR mode with Ctrl+Q
            await pilot.press("ctrl+q")
            await pilot.pause()
            assert login_screen.step == "qr"

            # Simulate mobile scanning the QR code
            gw.simulate_qr_scan(login_screen.qr_url)
            await pilot.pause()

            # Should return to AccountsScreen
            assert isinstance(app.screen, AccountsScreen)


# ---------------------------------------------------------------------------
# 6. AccountsScreen Fast Switching & Stage 2 Hotkeys (1-9, S, P)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accounts_screen_number_selection():
    gw = FakeGateway(chat_count=10)
    app = TelegramCLIApp(gateway=gw)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, AccountsScreen)

        # Press '2' to instantly open account 2 (Work)
        await pilot.press("2")
        await pilot.pause()
        assert isinstance(app.screen, ChatsScreen)
        chats_screen: ChatsScreen = app.screen
        assert chats_screen.account.username == "work"

        # Esc returns to AccountsScreen
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, AccountsScreen)


@pytest.mark.asyncio
async def test_accounts_screen_sessions_and_profile_hotkeys():
    gw = FakeGateway(chat_count=10)
    app = TelegramCLIApp(gateway=gw)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, AccountsScreen)

        # Press 'S' to open SessionsScreen
        await pilot.press("S")
        await pilot.pause()
        assert isinstance(app.screen, SessionsScreen)
        assert len(app.screen.sessions_list.items) > 0

        # Press Esc to return
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, AccountsScreen)

        # Press 'P' to open ProfileScreen
        await pilot.press("P")
        await pilot.pause()
        assert isinstance(app.screen, ProfileScreen)

        # Press Esc to return
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, AccountsScreen)


# ---------------------------------------------------------------------------
# 7. SessionsScreen Revocation Pilot Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sessions_screen_revocation_flow():
    gw = FakeGateway(chat_count=5)
    acc = gw.get_accounts()[0]

    app = TelegramCLIApp(gateway=gw)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SessionsScreen(gateway=gw, account=acc)
        app.push_screen(screen)
        await pilot.pause()

        assert len(screen.sessions_list.items) >= 2
        # Cursor on item 0 (current session) -> pressing 'd' should warn
        screen.sessions_list.cursor = 0
        await pilot.press("d")
        await pilot.pause()
        assert "Cannot revoke current" in (screen.status_bar.status_message or "")
        assert screen._pending_confirm is None

        # Move to item 1 (non-current session) -> pressing 'd' should prompt
        screen.sessions_list.cursor = 1
        await pilot.press("d")
        await pilot.pause()
        assert screen._pending_confirm is not None
        assert "(y/n)" in (screen.status_bar.prompt_message or "")

        # Confirm with 'y'
        initial_count = len(screen.sessions_list.items)
        await pilot.press("y")
        await pilot.pause()
        assert screen._pending_confirm is None
        assert len(screen.sessions_list.items) == initial_count - 1
        assert "Revoked session" in (screen.status_bar.status_message or "")


# ---------------------------------------------------------------------------
# 8. ProfileScreen Bio & Username Editing Pilot Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_screen_edit_bio_and_username():
    gw = FakeGateway(chat_count=5)
    acc = gw.get_accounts()[0]

    app = TelegramCLIApp(gateway=gw)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ProfileScreen(gateway=gw, account=acc)
        app.push_screen(screen)
        await pilot.pause()

        edit_box = screen.query_one("#profile-edit-box")
        assert edit_box.display is False

        # Press 'b' to edit bio
        await pilot.press("b")
        await pilot.pause()
        assert screen._edit_mode == "bio"
        assert edit_box.display is True

        # Enter new bio and submit
        screen.edit_input.value = "New Pilot Bio"
        await pilot.press("enter")
        await pilot.pause()
        assert screen._edit_mode is None
        assert edit_box.display is False
        assert gw.get_user_profile(acc.user_id).bio == "New Pilot Bio"
        assert "Bio updated" in (screen.status_bar.status_message or "")

        # Press 'u' to edit username
        await pilot.press("u")
        await pilot.pause()
        assert screen._edit_mode == "username"
        assert edit_box.display is True

        # Enter new username and submit
        screen.edit_input.value = "@pilot_alice"
        await pilot.press("enter")
        await pilot.pause()
        assert screen._edit_mode is None
        assert edit_box.display is False
        assert gw.get_user_profile(acc.user_id).username == "pilot_alice"
        assert "Username updated" in (screen.status_bar.status_message or "")


# ---------------------------------------------------------------------------
# 9. ChatsScreen Instant Account Switching (1-9, F2, Shift+F2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chats_screen_instant_account_switching():
    gw = FakeGateway(chat_count=10)
    app = TelegramCLIApp(gateway=gw)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Open Alice (account 1)
        await pilot.press("1")
        await pilot.pause()
        assert isinstance(app.screen, ChatsScreen)
        chats_screen: ChatsScreen = app.screen
        assert chats_screen.account.username == "alice"

        # Press '2' to switch to Work
        await pilot.press("2")
        await pilot.pause()
        assert chats_screen.account.username == "work"

        # Press F2 to switch to next account (Business)
        await pilot.press("f2")
        await pilot.pause()
        assert chats_screen.account.username == "business"

        # Press Shift+F2 to switch back to Work
        await pilot.press("shift+f2")
        await pilot.pause()
        assert chats_screen.account.username == "work"


# ---------------------------------------------------------------------------
# 10. Command Palette Proxy & Sessions Actions in ChatsScreen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chats_palette_proxy_and_sessions():
    gw = FakeGateway(chat_count=10)
    app = TelegramCLIApp(gateway=gw)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        chats_screen: ChatsScreen = app.screen

        # Open palette
        await pilot.press("ctrl+k")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
        palette: CommandPalette = app.screen

        # Search for proxy
        await pilot.press("p", "r", "o", "x", "y")
        await pilot.pause()
        # Execute Toggle Proxy
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ChatsScreen)
        assert "Proxy" in (chats_screen.status_bar.toast_message or "")
