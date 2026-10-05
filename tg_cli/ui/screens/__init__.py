"""Screens package for tg-cli."""
from tg_cli.ui.screens.accounts import AccountsScreen
from tg_cli.ui.screens.chats import ChatsScreen
from tg_cli.ui.screens.messages import MessagesScreen
from tg_cli.ui.screens.palette import CommandPalette
from tg_cli.ui.screens.help import HelpScreen

__all__ = [
    "AccountsScreen",
    "ChatsScreen",
    "MessagesScreen",
    "CommandPalette",
    "HelpScreen",
]
