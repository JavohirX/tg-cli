"""Tests for commands registry."""

from tg_cli.commands import COMMANDS, get_command_by_id, get_commands_for_context, get_footer_hints


def test_command_ids_are_unique():
    ids = [cmd.id for cmd in COMMANDS]
    assert len(ids) == len(set(ids)), "Command IDs must be unique"


def test_get_command_by_id():
    cmd = get_command_by_id("palette")
    assert cmd is not None
    assert cmd.title == "Command Palette"
    assert "ctrl+k" in cmd.keys


def test_get_commands_for_context():
    chat_cmds = get_commands_for_context("chats")
    chat_cmd_ids = {c.id for c in chat_cmds}
    assert "open_chat" in chat_cmd_ids
    assert "archive_chat" in chat_cmd_ids
    assert "palette" in chat_cmd_ids  # global command included


def test_footer_hints():
    hints_chats = get_footer_hints("chats")
    assert any(key == "Enter" for key, _ in hints_chats)

    hints_write = get_footer_hints("messages", is_write_mode=True)
    assert any(key == "Ctrl+J" for key, _ in hints_write)
