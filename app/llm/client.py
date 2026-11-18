"""One entry point for every model call.

- picks the model from the task (router.py)
- puts the stable context (SOPs, profile, CV facts) first and caches it
- returns plain text or a validated Pydantic object
- records tokens and cost, and refuses to run past the daily budget
"""
from datetime import date, datetime, time
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import func, select

from app.config import get_settings
from app.db.base import session_scope
from app.db.models import LLMCall
from app.llm.pricing import cost_usd
from app.llm.router import model_for

T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(RuntimeError):
    pass


class LLMOutputError(RuntimeError):
    """The model did not return a valid structured answer, even after a retry."""


class LLMRefused(LLMOutputError):
    """The model declined this input (stop_reason=refusal). Retrying gives the same answer."""


def spent_today() -> float:
    start = datetime.combine(date.today(), time.min)
    with session_scope() as s:
        return float(s.scalar(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0))
                              .where(LLMCall.created_at >= start)) or 0.0)


class LLMClient:
    def __init__(self, sdk_client: Any | None = None, run_id: int | None = None):
        if sdk_client is None:
            import anthropic
            sdk_client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key or None)
        self._sdk = sdk_client
        self.run_id = run_id

    def _system(self, context: list[str], instructions: str | None) -> list[dict]:
        """Stable context first (cached), task-specific instructions after it."""
        blocks = [{"type": "text", "text": c} for c in context if c]
        if blocks:
            blocks[-1]["cache_control"] = {"type": "ephemeral"}
        if instructions:
            blocks.append({"type": "text", "text": instructions})
        return blocks

    def _guard(self):
        limit = get_settings().daily_budget_usd
        if limit and spent_today() >= limit:
            raise BudgetExceeded(f"daily LLM budget of ${limit:.2f} reached")

    def _record(self, task: str, model: str, usage) -> float:
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = cost_usd(model, inp, out, cr, cw)
        with session_scope() as s:
            s.add(LLMCall(run_id=self.run_id, task=task, model=model, input_tokens=inp,
                          output_tokens=out, cache_read_tokens=cr, cache_write_tokens=cw, cost_usd=cost))
        return cost

    def complete(self, task: str, prompt: str | list[dict], *, context: list[str] = (),
                 instructions: str | None = None, output: type[T] | None = None) -> str | T:
        self._guard()
        model, max_tokens = model_for(task)
        messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
        kwargs = dict(model=model, max_tokens=max_tokens, messages=messages,
                      system=self._system(list(context), instructions))
        if output is not None:
            from pydantic import ValidationError
            problem = "?"
            for attempt in range(3):
                try:
                    resp = self._sdk.messages.parse(output_format=output, **kwargs)
                except ValidationError as err:
                    # the SDK validates the JSON itself: a cut-off or malformed answer lands here
                    problem = "invalid output: " + "; ".join(
                        f"{'.'.join(map(str, e.get('loc', ()))) or 'answer'}: {e.get('msg', e.get('type', '?'))}"
                        for e in err.errors()[:3])
                else:
                    self._record(task, model, resp.usage)
                    if resp.parsed_output is not None:
                        return resp.parsed_output
                    if getattr(resp, "stop_reason", None) == "refusal":
                        raise LLMRefused(f"{task}: the model declined to answer (stop_reason=refusal)")
                    problem = f"stop_reason={getattr(resp, 'stop_reason', '?')}"
                # try again with more room to finish
                kwargs["max_tokens"] = kwargs["max_tokens"] * 2
            raise LLMOutputError(f"{task}: no valid {output.__name__} after 3 attempts ({problem})")
        resp = self._sdk.messages.create(**kwargs)
        self._record(task, model, resp.usage)
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
