"""One undoable action with a countdown toast in the status bar."""

from __future__ import annotations

import time
from typing import Callable

from tg_cli.ui.chrome import StatusBar

UNDO_SECONDS = 5.0


class UndoSlot:
    """Holds the last action until its window closes. A new action replaces it.

    The screen calls tick() once a second so the toast counts down.
    """

    def __init__(self, status_bar: StatusBar, seconds: float = UNDO_SECONDS) -> None:
        self.status_bar = status_bar
        self.seconds = seconds
        self._pending: tuple[str, Callable[[], None], float] | None = None

    @property
    def active(self) -> bool:
        return self._pending is not None

    def set(self, description: str, undo_fn: Callable[[], None]) -> None:
        self._pending = (description, undo_fn, time.time() + self.seconds)
        self.status_bar.set_toast(f"{description}. Press 'u' to undo ({int(self.seconds)}s)")

    def undo(self) -> bool:
        """Run the undo. Returns False when there was nothing to undo."""
        if self._pending is None:
            return False
        description, undo_fn, _ = self._pending
        self._pending = None
        undo_fn()
        self.status_bar.set_toast(f"Undone: {description}")
        return True

    def tick(self) -> None:
        if self._pending is None:
            return
        description, _, expiry = self._pending
        remaining = int(expiry - time.time())
        if remaining <= 0:
            self._pending = None
            self.status_bar.set_toast(None)
        else:
            self.status_bar.set_toast(f"{description}. Press 'u' to undo ({remaining}s)")
