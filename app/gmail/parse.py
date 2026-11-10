"""Turn a Gmail API message into plain fields and clean body text."""
import base64
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr

MAX_BODY = 8000


@dataclass
class ParsedEmail:
    gmail_id: str
    thread_id: str
    sender: str
    sender_name: str
    to: list[str]
    cc: list[str]
    subject: str
    sent_at: datetime
    message_id: str | None
    body: str
    direction: str
    labels: list[str] = field(default_factory=list)
    auto_reply: bool = False

    @property
    def sender_domain(self) -> str:
        return self.sender.rsplit("@", 1)[-1]


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _walk(part: dict):
    yield part
    for p in part.get("parts", []) or []:
        yield from _walk(p)


def _html_to_text(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", "", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", h)
    return html.unescape(re.sub(r"<[^>]+>", "", h))


_AUTO_SUBJECT = re.compile(r"^\s*(automatic reply|auto[- ]?reply|autoreply|out of (the )?office|auto:|abwesenheit|risposta automatica)", re.I)


def is_auto_reply(headers: dict, subject: str) -> bool:
    auto = headers.get("auto-submitted", "").lower()
    if auto and auto != "no":
        return True
    if any(h in headers for h in ("x-autoreply", "x-autorespond")):
        return True
    if headers.get("precedence", "").lower() == "auto_reply":
        return True
    return bool(_AUTO_SUBJECT.search(subject or ""))


_QUOTE_START = re.compile(r"^(On .{5,200}wrote:|-{2,}\s*Original Message\s*-{2,}|From: .+|_{5,})\s*$", re.M)


def strip_quoted(text: str) -> str:
    """Drop the quoted earlier conversation below a reply."""
    m = _QUOTE_START.search(text)
    if m and m.start() > 0:
        text = text[: m.start()]
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def body_text(payload: dict) -> str:
    plain, rich = [], []
    for part in _walk(payload):
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        if part.get("mimeType") == "text/plain":
            plain.append(_decode(data))
        elif part.get("mimeType") == "text/html":
            rich.append(_html_to_text(_decode(data)))
    text = "\n".join(plain) if plain else "\n".join(rich)
    return strip_quoted(text)[:MAX_BODY]


def parse_message(raw: dict, me: str) -> ParsedEmail:
    headers = {h["name"].lower(): h["value"] for h in raw["payload"].get("headers", [])}
    name, sender = parseaddr(headers.get("from", ""))
    sender = sender.lower()
    to = [a.lower() for _, a in getaddresses([headers.get("to", "")]) if a]
    cc = [a.lower() for _, a in getaddresses([headers.get("cc", "")]) if a]
    sent = datetime.fromtimestamp(int(raw.get("internalDate", 0)) / 1000, tz=timezone.utc).replace(tzinfo=None)
    return ParsedEmail(
        gmail_id=raw["id"], thread_id=raw["threadId"], sender=sender, sender_name=name or sender,
        to=to, cc=cc, subject=headers.get("subject", ""), sent_at=sent,
        message_id=headers.get("message-id"), body=body_text(raw["payload"]),
        direction="out" if sender == me.lower() else "in", labels=raw.get("labelIds", []),
        auto_reply=is_auto_reply(headers, headers.get("subject", "")))
