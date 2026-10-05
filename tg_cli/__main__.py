"""Main entry point for running tg-cli: python -m tg_cli."""

from __future__ import annotations

import sys
from tg_cli.app import TelegramCLIApp
from tg_cli.telegram.fake import FakeGateway


def main() -> None:
    # In Phase 0, we run against the FakeGateway
    gateway = FakeGateway(chat_count=10_000)
    app = TelegramCLIApp(gateway=gateway)
    app.run()


if __name__ == "__main__":
    main()
