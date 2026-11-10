"""Fake Gmail and LLM for tests."""
import base64
from datetime import datetime, timezone


def make_msg(mid, thread, sender, to, subject, body, when: datetime, html=False, message_id=None, headers=None):
    data = base64.urlsafe_b64encode(body.encode()).decode()
    return {
        "id": mid, "threadId": thread, "labelIds": ["INBOX"],
        "internalDate": str(int(when.replace(tzinfo=timezone.utc).timestamp() * 1000)),
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "From", "value": sender}, {"name": "To", "value": to},
                        {"name": "Subject", "value": subject},
                        {"name": "Message-ID", "value": message_id or f"<{mid}@mail>"}]
                       + [{"name": k, "value": v} for k, v in (headers or {}).items()],
            "parts": [{"mimeType": "text/html" if html else "text/plain", "body": {"data": data}}],
        },
    }


class FakeGmail:
    def __init__(self, messages, me="farbodfooladi@gmail.com"):
        self.me = me
        self.messages = {m["id"]: m for m in messages}
        self.queries = []

    def search(self, query, limit=2000):
        self.queries.append(query)
        return list(self.messages)

    def get(self, mid):
        return self.messages[mid]


class FakeLLM:
    """Returns queued outputs per task; records calls."""
    def __init__(self, outputs: dict):
        self.outputs = {k: list(v) for k, v in outputs.items()}
        self.calls = []

    def complete(self, task, prompt, *, context=(), instructions=None, output=None):
        self.calls.append((task, prompt))
        return self.outputs[task].pop(0)
