"""Right-to-left text for Telegram that mixes Persian with English words, emails and numbers.

Each Persian line starts with a right-to-left mark, and every run of English inside it is isolated
(LRI ... PDI), so the line reads right to left and the English words stay intact and in place.
Lines with no Persian (the English email itself) are left as they are. Output is Telegram HTML.
"""
import html
import re

RLM, LRI, PDI = "‏", "⁦", "⁩"
_PERSIAN = re.compile(r"[؀-ۿﭐ-﷿ﹰ-﻿]")
_TOKEN = r"[A-Za-z0-9@#$(\[][A-Za-z0-9@#$%&_+=/\\.:;'\"!?()\[\]<>*~-]*"     # starts with a letter, digit or @#$
_LATIN_RUN = re.compile(rf"{_TOKEN}(?:[ ,]+{_TOKEN})*")


def rtl_line(line: str) -> str:
    if not _PERSIAN.search(line):
        return line
    return RLM + _LATIN_RUN.sub(lambda m: LRI + m.group(0) + PDI, line)


def rtl(text: str) -> str:
    return "\n".join(rtl_line(line) for line in text.split("\n"))


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def fa(text: str) -> str:
    """Persian (mixed) text, escaped for HTML and shaped right to left."""
    return esc(rtl(text))


def bold(text: str) -> str:
    return f"<b>{fa(text)}</b>"


def quote(text: str) -> str:
    """The English email, set apart in a quote block (stays left to right)."""
    return f"<blockquote>{esc(text)}</blockquote>"
