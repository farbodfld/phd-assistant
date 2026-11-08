from types import SimpleNamespace

import pytest
from pydantic import BaseModel


class FakeMessages:
    def __init__(self):
        self.calls = []

    def _resp(self, **extra):
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200,
                                cache_read_input_tokens=5000, cache_creation_input_tokens=0)
        return SimpleNamespace(usage=usage, content=[SimpleNamespace(type="text", text="hello")], **extra)

    def create(self, **kw):
        self.calls.append(("create", kw))
        return self._resp()

    def parse(self, **kw):
        self.calls.append(("parse", kw))
        return self._resp(parsed_output=kw["output_format"](category="positive"))


class Category(BaseModel):
    category: str


@pytest.fixture
def fake(db):
    from app.llm.client import LLMClient
    msgs = FakeMessages()
    return LLMClient(SimpleNamespace(messages=msgs)), msgs


def test_routes_cheap_and_strong(fake):
    client, msgs = fake
    client.complete("classify_email", "x")
    client.complete("initial_email", "x")
    assert msgs.calls[0][1]["model"] == "claude-haiku-5-5"
    assert msgs.calls[1][1]["model"] == "claude-opus-5-5"


def test_context_is_cached_and_instructions_follow(fake):
    client, msgs = fake
    client.complete("initial_email", "x", context=["SOP text", "profile"], instructions="write it")
    system = msgs.calls[0][1]["system"]
    assert system[1]["cache_control"] == {"type": "ephemeral"}   # last stable block
    assert "cache_control" not in system[2]                         # task part not cached


def test_structured_output(fake):
    client, _ = fake
    out = client.complete("classify_email", "x", output=Category)
    assert out.category == "positive"


def test_cost_is_recorded(fake):
    from app.db.base import session_scope
    from app.db.models import LLMCall
    client, _ = fake
    client.complete("initial_email", "x")
    with session_scope() as s:
        call = s.query(LLMCall).one()
    # 1000*4 + 200*20 + 5000*0.20 per million
    assert call.cost_usd == pytest.approx((4000 + 4000 + 1000) / 1e6)


def test_budget_guard(fake, monkeypatch):
    from app.config import get_settings
    from app.llm.client import BudgetExceeded
    client, _ = fake
    monkeypatch.setattr(get_settings(), "daily_budget_usd", 0.000001)
    client.complete("initial_email", "x")          # first call allowed
    with pytest.raises(BudgetExceeded):
        client.complete("initial_email", "x")


def test_unknown_task_is_rejected(fake):
    client, _ = fake
    with pytest.raises(KeyError):
        client.complete("not_a_task", "x")
