"""Links in a message, and which of them are safe to hand to the OS."""

from __future__ import annotations

import re

from tg_cli.domain.models import Message

_URL_RE = re.compile(r"(?:https?://|tg://|www\.)[^\s<>\"']+", re.IGNORECASE)

# On Windows webbrowser.open ends in os.startfile, which also runs programs
# and opens local paths. A message must not be able to do that.
SAFE_SCHEMES = ("http://", "https://", "tg://", "mailto:")


def is_safe_link(url: str) -> bool:
    return url.lower().startswith(SAFE_SCHEMES)


def _trim(url: str) -> str:
    """Drop punctuation that ends the sentence, not the link.

    A closing parenthesis stays when the link opened one, as in
    https://en.wikipedia.org/wiki/Python_(programming_language).
    """
    while url:
        last = url[-1]
        if last in ".,;:!?'\"]}>":
            url = url[:-1]
        elif last == ")" and url.count("(") < url.count(")"):
            url = url[:-1]
        else:
            break
    return url


def message_links(message: Message) -> list[str]:
    """Safe links in reading order: hidden text links first, then bare URLs."""
    found: list[str] = []
    for span in message.entity_spans:
        if span.get("type") == "text_url" and span.get("url"):
            found.append(str(span["url"]))
    for text in (message.plain_text, message.caption):
        for match in _URL_RE.finditer(text or ""):
            url = _trim(match.group(0))
            if url.lower().startswith("www."):
                url = "https://" + url
            found.append(url)
    unique = list(dict.fromkeys(found))
    return [url for url in unique if is_safe_link(url)]
