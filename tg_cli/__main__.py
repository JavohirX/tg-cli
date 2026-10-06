"""Main entry point for running tg-cli: python -m tg_cli."""

from __future__ import annotations

import argparse
import logging
from tg_cli.app import TelegramCLIApp
from tg_cli.paths import get_app_data_dir, get_db_path
from tg_cli.telegram.fake import FakeGateway
from tg_cli.telegram.telethon_gw import TelethonGateway


def setup_logging(debug: bool = False) -> None:
    log_dir = get_app_data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "tg-cli.log"

    level = logging.DEBUG if debug else logging.INFO
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)


def setup_windows_console() -> None:
    """Ensure UTF-8 code page and disable QuickEdit freeze in Windows cmd.exe / conhost."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # Set UTF-8 code page (CP_UTF8 = 65001)
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)

        # Disable QuickEdit mode to prevent process freezing on mouse click in cmd.exe
        ENABLE_QUICK_EDIT_MODE = 0x0040
        ENABLE_EXTENDED_FLAGS = 0x0080
        STD_INPUT_HANDLE = -10
        h_in = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(h_in, ctypes.byref(mode)):
            new_mode = (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS
            kernel32.SetConsoleMode(h_in, new_mode)
    except Exception:
        pass


def main() -> None:
    setup_windows_console()

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

    setup_logging(debug=args.debug)

    if args.fake:
        gateway = FakeGateway(chat_count=10_000)
        app = TelegramCLIApp(gateway=gateway)
        app.run()
        return

    # Telethon gateway mode with graceful database failure handling
    db_path = get_db_path()
    try:
        gateway = TelethonGateway()
        gateway.start()
        app = TelegramCLIApp(gateway=gateway)
        try:
            app.run()
        finally:
            gateway.stop()
    except Exception as exc:
        logging.exception("Failed to initialize database or gateway at %s", db_path)
        app = TelegramCLIApp(db_error=(db_path, str(exc)))
        app.run()


if __name__ == "__main__":
    main()
