from types import SimpleNamespace

import pytest

from app.mail.analysis import Relevance


class _Usage:
    input_tokens = 10; output_tokens = 10; cache_read_input_tokens = 0; cache_creation_input_tokens = 0


class _SDK:
    def __init__(self, results):
        self.results = list(results); self.max_tokens = []
        self.messages = self

    def parse(self, output_format, **kw):
        self.max_tokens.append(kw["max_tokens"])
        if self.results and self.results[0] == "broken":
            self.results.pop(0)
            Relevance.model_validate_json('{"relevant": true, "reas')     # raises like the SDK does
        return SimpleNamespace(parsed_output=self.results.pop(0), usage=_Usage(), stop_reason="max_tokens")


def test_truncated_output_is_retried_with_more_room(db):
    from app.llm.client import LLMClient
    sdk = _SDK([None, Relevance(relevant=False, reason="newsletter")])
    out = LLMClient(sdk_client=sdk).complete("email_relevance", "x", output=Relevance)
    assert out.relevant is False and sdk.max_tokens == [500, 1000]


def test_malformed_json_is_retried(db):
    from app.llm.client import LLMClient
    sdk = _SDK(["broken", Relevance(relevant=True, reason="admissions")])
    assert LLMClient(sdk_client=sdk).complete("email_relevance", "x", output=Relevance).relevant


def test_backfill_keeps_thread_when_model_cannot_decide(db):
    from app.llm.client import LLMClient
    from app.mail.inbox import _is_relevant
    from app.gmail.parse import parse_message
    from datetime import datetime
    from tests.fakes import make_msg
    e = parse_message(make_msg("a", "t", "dr.x@leeds.ac.uk", "me@x.com", "Hello", "body", datetime(2026, 10, 1)), "me@x.com")
    assert _is_relevant(LLMClient(sdk_client=_SDK([None, "broken", None])), e) is True
