"""Copy text onto the terminal clipboard and the Windows clipboard.

Textual writes OSC 52, which a terminal may ignore. On Windows the Win32
clipboard is what Ctrl+C in other programs reads.
"""

from __future__ import annotations

import sys
from typing import Any


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
