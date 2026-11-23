"""Which model does which job.

Rule of thumb: code first, Haiku for judging and sorting, Opus for anything
a professor or admissions officer will read, and for decisions that matter.
Change a task's tier here; nothing else in the code needs to change.
"""
from dataclasses import dataclass

from app.config import get_settings


@dataclass(frozen=True)
class Route:
    tier: str          # "cheap" | "strong"
    max_tokens: int


ROUTES: dict[str, Route] = {
    # cheap: classification, extraction, scoring, summaries
    "classify_email": Route("cheap", 600),
    "email_relevance": Route("cheap", 500),        # is an unknown academic email about Farbod's PhD search?
    "screen_listing": Route("cheap", 600),        # first-pass funding / topic filter
    "score_position": Route("cheap", 1500),
    "extract_requirements": Route("cheap", 800),
    "daily_report": Route("cheap", 1200),
    "chat_simple": Route("cheap", 800),           # "what are this week's deadlines?"

    # strong: anything sent to a person, and judgement calls
    "reply_to_supervisor": Route("strong", 1500),
    "analyse_email": Route("strong", 3000),        # every supervisor/admissions email: category, insights, actions, reply
    "extract_insights": Route("strong", 1200),
    "initial_email": Route("strong", 1200),
    "reminder_email": Route("strong", 500),
    "edit_email": Route("strong", 1500),          # Farbod's edit instruction from Telegram
    "personal_statement": Route("strong", 4000),
    "research_proposal": Route("strong", 6000),
    "risk_check": Route("strong", 1200),
    "portal_step": Route("strong", 2000),
    "chat": Route("strong", 2000),               # Farbod's conversation with the assistant in Telegram
}


def model_for(task: str) -> tuple[str, int]:
    route = ROUTES.get(task)
    if route is None:
        raise KeyError(f"unknown LLM task '{task}': add it to app/llm/router.py ROUTES")
    s = get_settings()
    model = s.model_strong if route.tier == "strong" else s.model_cheap
    return model, route.max_tokens
