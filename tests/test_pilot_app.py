"""End-to-end Textual pilot tests for tg-cli application."""

import pytest
from tg_cli.app import TelegramCLIApp
from tg_cli.telegram.fake import FakeGateway
from tg_cli.ui.screens.accounts import AccountsScreen
from tg_cli.ui.screens.chats import ChatsScreen
from tg_cli.ui.screens.help import HelpScreen
from tg_cli.ui.screens.messages import MessagesScreen
from tg_cli.ui.screens.palette import CommandPalette


@pytest.mark.asyncio
async def test_app_full_flow():
    gateway = FakeGateway(chat_count=100)
    app = TelegramCLIApp(gateway=gateway)

    async with app.run_test() as pilot:
        await pilot.pause()

        # Step 1: Initial screen should be AccountsScreen (since 3 accounts exist)
        assert isinstance(app.screen, AccountsScreen)

        # Step 2: Press Enter to open the first account (Alice)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ChatsScreen)
        chats_screen = app.screen
        assert chats_screen.account.username == "alice"

        # Check cursor starts on first real chat (index 1 because index 0 is [Archive])
        assert chats_screen.chat_list.cursor == 1

        # Step 3: j and k navigation in chat list
        await pilot.press("j")
        assert chats_screen.chat_list.cursor == 2
        await pilot.press("k")
        assert chats_screen.chat_list.cursor == 1

        # Step 4: Folder switching with [ and ]
        assert chats_screen.current_folder_id is None  # "All" tab
        await pilot.press("]")
        assert chats_screen.current_folder_id == 1  # "Personal" tab
        await pilot.press("[")
        assert chats_screen.current_folder_id is None

        # Step 5: Filter bar with /
        await pilot.press("/")
        filter_container = chats_screen.query_one("#filter-container")
        assert filter_container.display is True
        # Type a filter query
        await pilot.press("b", "o", "b")
        assert chats_screen.filter_query == "bob"
        # Esc closes filter
        await pilot.press("escape")
        assert filter_container.display is False
        assert chats_screen.filter_query == ""

        # Step 6: Command palette with Ctrl+K
        await pilot.press("ctrl+k")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ChatsScreen)

        # Step 7: Help screen with ?
        await pilot.press("?")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ChatsScreen)

        # Step 8: Enter on a chat opens MessagesScreen
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, MessagesScreen)
        msg_screen = app.screen

        # MessagesScreen starts in LIST focus mode
        assert msg_screen.is_write_mode is False

        # Step 9: Tab switches to WRITE mode (composer)
        await pilot.press("tab")
        assert msg_screen.is_write_mode is True

        # Send a message
        msg_screen.composer.text = "Hello from test!"
        await pilot.press("enter")
        await pilot.pause()
        # Ensure message is added
        last_msg = msg_screen.message_list.items[-1]
        assert last_msg.plain_text == "Hello from test!"

        # Tab or Esc back to list mode
        await pilot.press("escape")
        assert msg_screen.is_write_mode is False

        # Step 10: Esc pops back to ChatsScreen
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ChatsScreen)

        # Step 11: Esc pops back to AccountsScreen
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, AccountsScreen)
