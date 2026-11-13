"""Paste reads the system clipboard, and file groups follow the send rules."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.events import Paste
from tg_cli.domain.files import (
    describe_media_group,
    paste_disposition,
    upload_label,
    upload_progress_line,
    upload_weights,
)
from tg_cli.telegram.fake import FakeGateway
from tg_cli.ui.clipboard import ClipboardPayload
from tg_cli.ui.composer import Composer
from tg_cli.ui.paste import (
    SendMediaDialog,
    clipboard_text_for_widget,
    existing_file_paths,
)
from tg_cli.ui.screens.login import LoginScreen
from tg_cli.ui.screens.messages import MessagesScreen


def test_media_group_is_photos_videos_or_both():
    assert paste_disposition(["a.jpg", "b.PNG", "c.mp4"]) == "media"
    assert paste_disposition(["clip.mov"]) == "media"
    assert paste_disposition(["clip.webm", "still.heic"]) == "media"
    assert paste_disposition(["notes.pdf"]) == "files"
    assert paste_disposition(["a.jpg", "notes.pdf"]) == "files"
    assert paste_disposition(["song.mp3", "a.jpg"]) == "files"
    assert describe_media_group(["a.jpg", "b.png", "c.mp4"]) == "2 photos and 1 video"
    assert describe_media_group(["only.mov"]) == "1 video"


def test_upload_progress_line_and_label(tmp_path):
    assert upload_label([r"C:\shots\shot.jpg"], as_file=False) == "shot.jpg"
    assert upload_label(["a.pdf", "b.zip"], as_file=True) == "2 files"
    assert upload_label(["a.jpg", "b.png", "c.mp4"], as_file=False) == (
        "2 photos and 1 video as one album"
    )
    assert upload_progress_line("shot.jpg", 0, 100) == "Uploading shot.jpg: [░░░░░░░░░░] 0%"
    assert upload_progress_line("shot.jpg", 67, 100) == "Uploading shot.jpg: [██████░░░░] 67%"
    assert upload_progress_line("shot.jpg", 100, 100) == "Uploading shot.jpg: [██████████] 100%"
    small = tmp_path / "a.bin"
    large = tmp_path / "b.bin"
    small.write_bytes(b"x" * 100)
    large.write_bytes(b"y" * 300)
    assert upload_weights([str(small), str(large)]) == [100.0, 300.0]
    assert upload_weights([str(small), str(tmp_path / "missing.bin")]) == [1.0, 1.0]


def test_clipboard_text_keeps_password_spaces_and_message_lines():
    assert clipboard_text_for_widget(" secret pass \nignored", single_line=True) == " secret pass "
    assert clipboard_text_for_widget("+998 90\n123", single_line=True) == "+998 90"
    assert clipboard_text_for_widget("hello\nthere\n", single_line=False) == "hello\nthere"
    assert clipboard_text_for_widget("hello\n\n", single_line=False) == "hello\n"


def test_quoted_paths_are_files_and_a_sentence_is_not(tmp_path):
    photo = tmp_path / "a.jpg"
    video = tmp_path / "b.mp4"
    photo.write_bytes(b"jpg")
    video.write_bytes(b"mp4")
    pasted = f'"{photo}" "{video}"'
    assert existing_file_paths(pasted) == [str(photo), str(video)]
    assert existing_file_paths(f"see {photo}") == []
    assert existing_file_paths("hello there") == []
    assert existing_file_paths(str(photo)) == [str(photo)]


def _clip(monkeypatch, text: str = "", files: tuple[str, ...] | list[str] = ()) -> None:
    payload = ClipboardPayload(text=text, files=tuple(files))
    monkeypatch.setattr("tg_cli.ui.paste.read_clipboard", lambda: payload)


class _ComposerApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.composer = Composer()
        self.submitted: list[str] = []

    def compose(self) -> ComposeResult:
        yield self.composer

    def on_composer_message_submitted(self, event: Composer.MessageSubmitted) -> None:
        self.submitted.append(event.text)


class _LoginApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.gateway = FakeGateway(chat_count=1)

    def on_mount(self) -> None:
        self.push_screen(LoginScreen(gateway=self.gateway))


class _MessagesApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.gateway = FakeGateway(chat_count=5)
        self.account = self.gateway.get_accounts()[0]
        self.chat = self.gateway.get_chats(self.account.user_id)[0]

    def on_mount(self) -> None:
        self.push_screen(
            MessagesScreen(gateway=self.gateway, account=self.account, chat=self.chat)
        )


@pytest.mark.asyncio
async def test_password_phone_and_code_accept_a_paste(monkeypatch):
    _clip(monkeypatch, text=" secret pass \nnope")
    app = _LoginApp()
    async with app.run_test() as pilot:
        login = app.screen
        assert isinstance(login, LoginScreen)
        login.step = "password"
        login._update_step_ui()
        await pilot.pause()
        login.input_widget.focus()
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert login.input_widget.password is True
        assert login.input_widget.value == " secret pass "

        _clip(monkeypatch, text="+998 90 123 45 67\n")
        login.step = "phone"
        login._update_step_ui()
        await pilot.pause()
        login.input_widget.focus()
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert login.input_widget.value == "+998 90 123 45 67"

        _clip(monkeypatch, text="123456")
        login.step = "code"
        login._update_step_ui()
        await pilot.pause()
        login.input_widget.focus()
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert login.input_widget.value == "123456"


@pytest.mark.asyncio
async def test_composer_paste_inserts_lines_and_does_not_send(monkeypatch):
    _clip(monkeypatch, text="hello\nthere\n")
    app = _ComposerApp()
    async with app.run_test() as pilot:
        app.composer.focus_input()
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert app.composer.text == "hello\nthere"
        assert app.submitted == []

        app.composer.text = ""
        app.composer.text_area.post_message(Paste("from the terminal\n"))
        await pilot.pause()
        assert app.composer.text == "from the terminal"
        assert app.submitted == []


@pytest.mark.asyncio
async def test_text_paste_on_the_message_list_fills_the_composer(monkeypatch):
    _clip(monkeypatch, text="from the list")
    app = _MessagesApp()
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, MessagesScreen)
        screen.message_list.focus()
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert screen.composer.text == "from the list"
        assert screen.is_write_mode is True
        assert app.gateway.sent_files == []


@pytest.mark.asyncio
async def test_search_field_paste_stays_text_when_files_are_also_copied(monkeypatch, tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"jpg")
    _clip(monkeypatch, text="needle", files=[str(photo)])
    app = _MessagesApp()
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, MessagesScreen)
        screen.action_start_search()
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert screen.filter_input.value == "needle"
        assert app.gateway.sent_files == []
        assert isinstance(app.screen, MessagesScreen)


@pytest.mark.asyncio
async def test_mixed_file_paste_sends_as_files_without_asking(monkeypatch, tmp_path):
    photo = tmp_path / "shot.jpg"
    notes = tmp_path / "notes.pdf"
    photo.write_bytes(b"jpg")
    notes.write_bytes(b"pdf")
    _clip(monkeypatch, files=[str(photo), str(notes)])
    app = _MessagesApp()
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, MessagesScreen)
        screen.composer.text = "see these"
        screen.composer.set_reply_context(4, "Ada", "hi")
        screen._set_focus_mode(True)
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert isinstance(app.screen, MessagesScreen)
        sent = app.gateway.sent_files
        assert len(sent) == 1
        assert sent[0]["as_file"] is True
        assert sent[0]["paths"] == [str(photo), str(notes)]
        assert sent[0]["caption"] == "see these"
        assert sent[0]["reply_to_id"] == 4
        assert screen.composer.text == ""
        assert screen.composer.reply_to_id is None
        assert screen.status_bar.status_message == "Uploading 2 files: [░░░░░░░░░░] 0%"


@pytest.mark.asyncio
async def test_media_group_asks_then_sends_as_pictures_or_files(monkeypatch, tmp_path):
    photo = tmp_path / "one.jpg"
    other = tmp_path / "two.png"
    video = tmp_path / "clip.mp4"
    for path, data in ((photo, b"a"), (other, b"b"), (video, b"c")):
        path.write_bytes(data)
    paths = [str(photo), str(other), str(video)]
    _clip(monkeypatch, files=paths)
    app = _MessagesApp()
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, MessagesScreen)
        screen.message_list.focus()
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, SendMediaDialog)
        rendered = str(dialog.query_one("#send-media-title").render())
        assert "2 photos and 1 video" in rendered
        assert "one album" in str(dialog.query_one("#send-media-body").render())
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, MessagesScreen)
        assert app.gateway.sent_files[-1]["as_file"] is False
        assert app.gateway.sent_files[-1]["paths"] == paths
        assert screen.status_bar.status_message == (
            "Uploading 2 photos and 1 video as one album: [░░░░░░░░░░] 0%"
        )

    _clip(monkeypatch, files=paths)
    app = _MessagesApp()
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, MessagesScreen)
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert isinstance(app.screen, SendMediaDialog)
        await pilot.press("f")
        await pilot.pause()
        assert app.gateway.sent_files[-1]["as_file"] is True

    _clip(monkeypatch, files=paths)
    app = _MessagesApp()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert isinstance(app.screen, SendMediaDialog)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, MessagesScreen)
        assert app.gateway.sent_files == []
        assert app.screen.status_bar.status_message == "Send cancelled."


@pytest.mark.asyncio
async def test_path_text_paste_is_a_file_group_and_edit_blocks_it(monkeypatch, tmp_path):
    photo = tmp_path / "only.jpg"
    photo.write_bytes(b"jpg")
    _clip(monkeypatch, text=f'"{photo}"')
    app = _MessagesApp()
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, MessagesScreen)
        screen.composer.set_edit_context(1, "keep me")
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert app.gateway.sent_files == []
        assert "Finish editing" in screen.status_bar.status_message
        assert screen.composer.text == "keep me"

        screen.composer.clear_context()
        screen.composer.text = ""
        screen.message_list.focus()
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert isinstance(app.screen, SendMediaDialog)
        await pilot.press("escape")
        await pilot.pause()


@pytest.mark.asyncio
async def test_caption_over_1024_is_refused(monkeypatch, tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_bytes(b"txt")
    _clip(monkeypatch, files=[str(notes)])
    app = _MessagesApp()
    async with app.run_test() as pilot:
        screen = app.screen
        assert isinstance(screen, MessagesScreen)
        screen.composer.text = "x" * 1025
        screen.message_list.focus()
        await pilot.pause()
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert app.gateway.sent_files == []
        assert "1024" in screen.status_bar.status_message
        assert screen.composer.text == "x" * 1025
