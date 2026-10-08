"""ASCII and UTF-8 half-block QR code rendering for terminal screens.
Converts QR code matrix into compact UTF-8 block characters (▄, ▀, █, space).
"""

from __future__ import annotations

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def generate_qr_ascii(data: str, border: int = 1, invert: bool = True) -> list[str]:
    """Generate compact UTF-8 half-block QR code lines.

    Each line contains characters representing 2 vertical QR modules:
    - top=dark, bottom=dark: ' ' (if invert=True for dark terminal background)
    - top=dark, bottom=light: '▄'
    - top=light, bottom=dark: '▀'
    - top=light, bottom=light: '█'

    If invert=False (light terminal background), polarity is flipped.
    """
    if not data:
        return []

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=1,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    height = len(matrix)
    width = len(matrix[0]) if height > 0 else 0

    lines: list[str] = []
    # Step by 2 rows
    for r in range(0, height, 2):
        row_chars: list[str] = []
        for c in range(width):
            top_dark = matrix[r][c]
            bot_dark = matrix[r + 1][c] if (r + 1 < height) else False

            if invert:
                # Dark terminal background: dark module is background (empty/space)
                # Light/white module is foreground (block)
                if top_dark and bot_dark:
                    ch = " "
                elif top_dark and not bot_dark:
                    ch = "▄"
                elif not top_dark and bot_dark:
                    ch = "▀"
                else:
                    ch = "█"
            else:
                # Light terminal background: dark module is foreground (block)
                if top_dark and bot_dark:
                    ch = "█"
                elif top_dark and not bot_dark:
                    ch = "▀"
                elif not top_dark and bot_dark:
                    ch = "▄"
                else:
                    ch = " "
            row_chars.append(ch)
        lines.append("".join(row_chars))

    return lines


def render_progress_bar(remaining_s: int, total_s: int = 30, width: int = 16) -> str:
    """Render a text progress bar for QR token countdown."""
    fraction = max(0.0, min(1.0, remaining_s / max(1, total_s)))
    filled = int(fraction * width)
    empty = width - filled
    bar = "█" * filled + "-" * empty
    pct = int(fraction * 100)
    return f"Expires in {remaining_s}s [{bar}] {pct}%"
