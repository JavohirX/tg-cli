"""Main entry point for running tg-cli: python -m tg_cli."""

from __future__ import annotations

import argparse
import logging
from tg_cli.app import TelegramCLIApp
from tg_cli.telegram.fake import FakeGateway
from tg_cli.telegram.telethon_gw import TelethonGateway


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Keyboard-first Telegram client for Windows Terminal"
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Run with in-memory fake gateway and 10k sample chats",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Raise log level to DEBUG",
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.fake:
        gateway = FakeGateway(chat_count=10_000)
    else:
        gateway = TelethonGateway()
        gateway.start()

    app = TelegramCLIApp(gateway=gateway)
    try:
        app.run()
    finally:
        if isinstance(gateway, TelethonGateway):
            gateway.stop()


if __name__ == "__main__":
    main()
