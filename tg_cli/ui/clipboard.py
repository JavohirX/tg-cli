"""Copy and read the terminal clipboard and the Windows clipboard.

Textual writes OSC 52, which a terminal may ignore. On Windows the Win32
clipboard is what Ctrl+C and Ctrl+V in other programs use. Textual's own
clipboard only remembers text copied inside this process, so a paste has
to read CF_UNICODETEXT and CF_HDROP itself.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClipboardPayload:
    """Text and file paths currently on the system clipboard."""

    text: str = ""
    files: tuple[str, ...] = ()


def copy_text(app: Any, text: str) -> bool:
    """Copy `text`. Return True when at least one clipboard accepted it."""
    if not text:
        return False
    ok = False
    try:
        app.copy_to_clipboard(text)
        ok = True
    except Exception:
        ok = False
    if sys.platform == "win32" and _win32_set(text):
        ok = True
    return ok


def read_clipboard() -> ClipboardPayload:
    """Read text and file paths from the system clipboard."""
    if sys.platform == "win32":
        return _win32_read()
    return ClipboardPayload()


def _win32_read() -> ClipboardPayload:
    """Read CF_HDROP paths and CF_UNICODETEXT. Never frees clipboard memory."""
    import ctypes

    CF_UNICODETEXT = 13
    CF_HDROP = 15
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    shell32 = ctypes.windll.shell32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_int
    user32.CloseClipboard.argtypes = []
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    shell32.DragQueryFileW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_wchar_p,
        ctypes.c_uint,
    ]
    shell32.DragQueryFileW.restype = ctypes.c_uint

    if not user32.OpenClipboard(None):
        return ClipboardPayload()
    try:
        files: list[str] = []
        hdrop = user32.GetClipboardData(CF_HDROP)
        if hdrop:
            count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            for index in range(count):
                length = shell32.DragQueryFileW(hdrop, index, None, 0)
                buf = ctypes.create_unicode_buffer(length + 1)
                shell32.DragQueryFileW(hdrop, index, buf, length + 1)
                if buf.value:
                    files.append(buf.value)

        text = ""
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if handle:
            locked = kernel32.GlobalLock(handle)
            if locked:
                try:
                    text = ctypes.wstring_at(locked)
                finally:
                    kernel32.GlobalUnlock(handle)
        return ClipboardPayload(text=text, files=tuple(files))
    except Exception:
        return ClipboardPayload()
    finally:
        user32.CloseClipboard()


def _win32_set(text: str) -> bool:
    """Put Unicode text on the Windows clipboard. Handles stay pointer-sized."""
    import ctypes

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    data = text.encode("utf-16-le") + b"\x00\x00"
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(locked, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    except Exception:
        return False
    finally:
        user32.CloseClipboard()
