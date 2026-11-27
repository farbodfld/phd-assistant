"""Loads the knowledge files (profile, SOPs, institution rules) and builds LLM context."""
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import ROOT

KNOWLEDGE = ROOT / "knowledge"
SOPS = ROOT / "sops"

# Which SOPs each LLM task needs.
TASK_SOPS: dict[str, list[str]] = {
    "initial_email": ["10_outreach_emails", "40_funding_and_eligibility"],
    "reminder_email": ["20_reminders"],
    "edit_email": ["10_outreach_emails", "20_reminders", "25_replies"],
    "reply_to_supervisor": ["25_replies", "10_outreach_emails"],
    "extract_insights": ["25_replies", "40_funding_and_eligibility"],
    "classify_email": ["25_replies"],
    "email_relevance": [],
    "analyse_email": ["25_replies", "10_outreach_emails", "40_funding_and_eligibility"],
    "personal_statement": ["30_documents"],
    "research_proposal": ["30_documents"],
    "screen_listing": ["40_funding_and_eligibility"],
    "score_position": ["50_scoring", "40_funding_and_eligibility"],
    "risk_check": ["40_funding_and_eligibility", "70_approvals_and_notifications"],
    "portal_step": ["35_portals"],
    "portal_plan": ["35_portals", "30_documents"],
    "edit_document": ["30_documents"],
    "daily_report": ["70_approvals_and_notifications", "60_tracker"],
    "chat": ["70_approvals_and_notifications", "60_tracker"],
    "chat_simple": ["60_tracker"],
}
NEEDS_PROFILE = {"analyse_email", "initial_email", "edit_email", "reminder_email", "reply_to_supervisor", "personal_statement",
                 "research_proposal", "score_position", "portal_step", "chat", "edit_document"}


@lru_cache
def load_profile() -> dict:
    return yaml.safe_load((KNOWLEDGE / "profile.yaml").read_text(encoding="utf-8"))


def load_private() -> dict:
    """Personal identifiers for portal forms; empty if the local file is missing."""
    p = KNOWLEDGE / "private.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}


@lru_cache
def load_sops() -> dict[str, str]:
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(SOPS.glob("*.md"))}


@lru_cache
def load_institutions() -> list[dict]:
    return yaml.safe_load((KNOWLEDGE / "institutions.yaml").read_text(encoding="utf-8"))


def institutions_for(university: str | None) -> list[dict]:
    u = (university or "").lower()
    return [i for i in load_institutions() if any(m in u for m in i.get("match", []))]


def build_context(task: str) -> list[str]:
    """Stable context blocks for a task, in a fixed order so prompt caching hits."""
    sops = load_sops()
    names = TASK_SOPS.get(task, [])
    if task != "score_position":
        names = ["00_mission_and_hard_rules"] + names
    blocks = [f"## SOP {n}\n\n{sops[n]}" for n in names if n in sops]
    if task == "score_position":
        profile = load_profile()
        facts = {key: profile.get(key) for key in ("fee_status", "nationality", "projects", "languages_programming")}
        facts["degrees"] = [e["degree"] for e in profile.get("education", [])]
        facts["thesis_topics"] = [e[key]["title"] for e in profile.get("education", [])
                                 for key in ("thesis", "dissertation") if e.get(key, {}).get("title")]
        facts["experience"] = [e["role"] for e in profile.get("experience", [])]
        blocks.append("Use only these verified facts for fit and eligibility:\n"
                      + yaml.safe_dump(facts, allow_unicode=True, sort_keys=False))
    elif task in NEEDS_PROFILE:
        profile_text = (KNOWLEDGE / "profile.yaml").read_text(encoding="utf-8")
        blocks.append("## Farbod's verified profile (the only facts you may state)\n\n" + profile_text)
    return blocks
