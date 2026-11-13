"""Classify pasted paths and build the outgoing message for each one."""

from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from tg_cli.domain.models import Message, MessageKind, SendState

IMAGE_EXTENSIONS = frozenset({
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".jfif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
})
VIDEO_EXTENSIONS = frozenset({
    ".3gp",
    ".avi",
    ".flv",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ogv",
    ".webm",
    ".wmv",
})
# Telethon only uploads these extensions as photos. Anything else in an album
# is a document, and Telegram rejects a document album.
_ALBUM_PHOTO_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})
ALBUM_LIMIT = 10


def media_kind(path: str) -> str | None:
    """Return 'photo', 'video', or None when the path is not display media."""
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "photo"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


def paste_disposition(paths: Sequence[str]) -> str:
    """Return 'media' when every path is a photo or video, otherwise 'files'.

    Photos and videos together are one media group. A document, archive, or
    any other file makes the whole paste go out as files.
    """
    if paths and all(media_kind(path) for path in paths):
        return "media"
    return "files"


def upload_label(paths: Sequence[str], *, as_file: bool) -> str:
    """Short name for an upload status line."""
    if len(paths) == 1:
        return Path(paths[0]).name or str(paths[0])
    if as_file:
        return f"{len(paths)} files"
    label = describe_media_group(paths)
    if len(paths) > ALBUM_LIMIT:
        return f"{label} as albums"
    return f"{label} as one album"


def upload_weights(paths: Sequence[str]) -> list[float]:
    """Byte size of each path.

    When any size cannot be read, every file counts as one. A missing file
    must not make the rest of the bar look finished.
    """
    sizes: list[int | None] = []
    for path in paths:
        try:
            sizes.append(Path(path).stat().st_size)
        except OSError:
            sizes.append(None)
    if any(size is None for size in sizes):
        return [1.0] * len(paths)
    return [float(size or 0) for size in sizes]


def upload_progress_line(label: str, current: float, total: float, *, width: int = 10) -> str:
    """One status line, for example 'Uploading shot.jpg: [██████░░░░] 67%'."""
    if total <= 0:
        fraction = 1.0 if current > 0 else 0.0
    else:
        fraction = max(0.0, min(1.0, current / total))
    filled = width if fraction >= 1 else int(fraction * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"Uploading {label}: [{bar}] {int(fraction * 100)}%"


def describe_media_group(paths: Sequence[str]) -> str:
    """Describe a photo/video paste, for example '2 photos and 1 video'."""
    photos = 0
    videos = 0
    for path in paths:
        kind = media_kind(path)
        if kind == "video":
            videos += 1
        elif kind == "photo":
            photos += 1
    parts: list[str] = []
    if photos:
        parts.append(f"{photos} photo" if photos == 1 else f"{photos} photos")
    if videos:
        parts.append(f"{videos} video" if videos == 1 else f"{videos} videos")
    if parts:
        return " and ".join(parts)
    count = len(paths)
    return "1 file" if count == 1 else f"{count} files"


def album_safe(path: str) -> bool:
    """True when Telethon can put this file in a photo or video album."""
    suffix = Path(path).suffix.lower()
    if suffix in _ALBUM_PHOTO_EXTENSIONS:
        return True
    mime = mimetypes.guess_type(path)[0] or ""
    return mime.startswith("video/")


def send_groups(paths: Sequence[str], *, as_file: bool) -> list[list[str]]:
    """Each inner list is one Telegram request, in paste order.

    Documents go one at a time. Photos and videos share an album of at most
    ten. A webp, heic, gif, or other picture is sent on its own so it cannot
    make the album fail.
    """
    if as_file:
        return [[path] for path in paths]
    groups: list[list[str]] = []
    current: list[str] = []
    for path in paths:
        if album_safe(path):
            current.append(path)
            if len(current) == ALBUM_LIMIT:
                groups.append(current)
                current = []
            continue
        if current:
            groups.append(current)
            current = []
        groups.append([path])
    if current:
        groups.append(current)
    return groups


def message_kind_for_send(path: str, *, as_file: bool) -> MessageKind:
    """Kind stored for a queued send. `as_file` forces a document."""
    if as_file:
        return MessageKind.FILE
    kind = media_kind(path)
    if kind == "video":
        return MessageKind.VIDEO
    if kind == "photo":
        return MessageKind.PHOTO
    return MessageKind.FILE


def build_outgoing_file_message(
    *,
    account_id: int,
    chat_id: int,
    message_id: int,
    path: str,
    as_file: bool,
    caption: str,
    reply_to_id: int | None,
    send_state: SendState,
) -> Message:
    """One optimistic or fake row for a file that is about to be sent."""
    kind = message_kind_for_send(path, as_file=as_file)
    if kind == MessageKind.FILE:
        name = Path(path).name or path
        plain = f"{name} — {caption}" if caption else name
        stored_caption = caption
    else:
        plain = ""
        stored_caption = caption
    return Message(
        account_id=account_id,
        chat_id=chat_id,
        message_id=message_id,
        sender_id=account_id,
        sender_name="Me",
        date=datetime.now(timezone.utc),
        kind=kind,
        plain_text=plain,
        caption=stored_caption,
        reply_id=reply_to_id,
        outgoing=True,
        send_state=send_state,
        local_path=str(path),
    )
