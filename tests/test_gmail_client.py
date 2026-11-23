import json

import httplib2
import pytest
from googleapiclient.errors import HttpError

from app.gmail.client import GmailClient


def _err(status, reason):
    body = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()
    return HttpError(httplib2.Response({"status": status}), body)


class Req:
    def __init__(self, results):
        self.results = list(results)

    def execute(self):
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class Svc:
    def __init__(self, get_results):
        self.get_results = get_results

    def users(self):
        return self

    def getProfile(self, userId):
        return Req([{"emailAddress": "Me@Gmail.com"}])

    def messages(self):
        return self

    def get(self, **kw):
        return Req(self.get_results)


def test_retries_on_rate_limit_then_succeeds():
    slept = []
    g = GmailClient(Svc([_err(403, "rateLimitExceeded"), _err(429, "rateLimitExceeded"), {"id": "x"}]),
                    sleep=slept.append)
    assert g.me == "me@gmail.com"
    assert g.get("x") == {"id": "x"}
    assert [d for d in slept if d >= 1] == [2.0, 4.0]          # exponential backoff


def test_does_not_retry_permission_errors():
    g = GmailClient(Svc([_err(403, "insufficientPermissions")]), sleep=lambda d: None)
    with pytest.raises(HttpError):
        g.get("x")


def test_a_stale_connection_is_dropped_and_the_call_retried():
    import ssl
    from app.gmail.client import GmailClient

    class Conn:
        closed = False

        def close(self):
            Conn.closed = True

    class Inner:
        connections = {"https:gmail.googleapis.com": Conn()}

    class Req:
        http = type("Authorized", (), {"http": Inner()})()
        calls = 0

        def execute(self):
            Req.calls += 1
            if Req.calls == 1:
                raise ssl.SSLError("[SYS] unknown error")
            return {"id": "ok"}

    g = GmailClient.__new__(GmailClient)
    g._sleep, g._last = (lambda *_: None), 0.0
    assert g._exec(Req()) == {"id": "ok"} and Conn.closed and Inner.connections == {}
