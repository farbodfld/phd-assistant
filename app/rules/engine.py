"""Runs the institution rules, funding and deadline checks, and insights over positions,
and keeps one up-to-date flag per (position, kind)."""
from dataclasses import dataclass, field
from datetime import date, timedelta

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ROOT
from app.db.models import Application, Flag, Insight, Position
from app.knowledge.loader import institutions_for, load_institutions

ENGINE_KINDS = {
    "application_limit", "ai_policy", "funding_restricted", "funding_unclear", "funding_competition",
    "funding_excluded", "english_requirement", "entry_requirement", "process", "location",
    "deadline_passed", "deadline_soon", "insight",
}


@dataclass
class FlagSpec:
    kind: str
    message: str
    action: str                     # inform | followup | decide | block
    due_on: date | None = None


@dataclass
class CheckReport:
    flags_by_action: dict[str, int] = field(default_factory=dict)
    decisions: list[str] = field(default_factory=list)


def _is_active(p: Position) -> bool:
    return not p.closed


def _not_yet_applied(p: Position) -> bool:
    return _is_active(p) and not p.portal_done


def _open_candidates(positions: list[Position], today: date) -> list[Position]:
    return [p for p in positions if _not_yet_applied(p) and (p.deadline_date is None or p.deadline_date >= today)]


def position_flags(p: Position, insights: list[Insight], today: date) -> list[FlagSpec]:
    out: list[FlagSpec] = []
    for inst in institutions_for(p.university):
        name = inst["name"]
        if inst.get("exclude_reason"):
            out.append(FlagSpec("location", inst["exclude_reason"], "block"))
        if inst.get("ai_writing") == "banned" and _not_yet_applied(p):
            out.append(FlagSpec("ai_policy", f"{name} bans AI-written statements and proposals. "
                                "The agent will only prepare a reference draft; Farbod writes his own.", "inform"))
        if inst.get("funding_warning") and _is_active(p):
            out.append(FlagSpec("funding_restricted", inst["funding_warning"].strip(), "inform"))
        if inst.get("funding_ambiguity") and _not_yet_applied(p):
            out.append(FlagSpec("funding_unclear", inst["funding_ambiguity"].strip()
                                + " The agent will add this question to the email.", "followup", p.deadline_date))
        eng = inst.get("english")
        if isinstance(eng, dict) and _not_yet_applied(p):
            out.append(FlagSpec("english_requirement",
                                f"{name}: {eng['test']} {eng['overall']} overall ({eng['each']} each). "
                                f"Farbod has no IELTS. {eng.get('note', '')}".strip(), "inform", p.deadline_date))
        if inst.get("entry_requirement") and _not_yet_applied(p):
            out.append(FlagSpec("entry_requirement", f"{name}: {inst['entry_requirement']}", "inform"))
        if inst.get("process_note") and _not_yet_applied(p):
            out.append(FlagSpec("process", f"{name}: {inst['process_note']}", "inform"))

    # funding from the advert text (skip if an institution rule already covers it)
    kinds = {f.kind for f in out}
    if _not_yet_applied(p):
        if p.funding == "unclear" and "funding_unclear" not in kinds:
            out.append(FlagSpec("funding_unclear", "Funding wording does not confirm international fees. "
                                "The agent will ask in the email.", "followup", p.deadline_date))
        elif p.funding == "competition":
            out.append(FlagSpec("funding_competition", "Competition funded: funding is not guaranteed even if offered a place.", "inform"))
        elif p.funding in ("home_only", "partial"):
            out.append(FlagSpec("funding_excluded", "Funding does not cover international fees in full; "
                                "should be greyed and moved to excluded.", "decide"))

    # deadlines
    if _not_yet_applied(p) and p.deadline_date:
        if p.deadline_date < today:
            out.append(FlagSpec("deadline_passed", f"Deadline {p.deadline_text} has passed; grey this row?", "decide"))
        elif p.deadline_date <= today + timedelta(days=7):
            days = (p.deadline_date - today).days
            out.append(FlagSpec("deadline_soon", f"Deadline in {days} day(s): {p.deadline_text}.", "inform", p.deadline_date))

    # insights (position scope by title, university scope by name)
    title, uni = p.title.lower(), (p.university or "").lower()
    notes = [i for i in insights if i.active and (
        (i.scope_type == "position" and i.scope_value.lower() in title) or
        (i.scope_type == "university" and i.scope_value.lower() in uni))]
    if notes and _is_active(p):
        text = " | ".join(f"{i.source_ref or i.source} ({i.learned_on}): {i.text}" for i in notes)
        out.append(FlagSpec("insight", text, "inform"))
    return out


def limit_flags(positions: list[Position], applications: list[Application], today: date
                ) -> tuple[dict[int, FlagSpec], list[str]]:
    """Institution application limits: if open candidates would exceed what is left, ask Farbod."""
    per_position: dict[int, FlagSpec] = {}
    decisions = []
    for inst in load_institutions():
        lim = inst.get("application_limit")
        if not lim:
            continue
        match = inst.get("match", [])
        titles = [t.lower() for t in lim.get("title_match", [])]
        mine = [p for p in positions if any(m in (p.university or "").lower() for m in match)
                and (not titles or any(t in p.title.lower() for t in titles))]
        used = [a for a in applications if a.outcome in ("pending", "offer")
                and any(m in (a.university or "").lower() for m in match)
                and (not titles or any(t in (a.project or "").lower() for t in titles))]
        candidates = _open_candidates(mine, today)
        left = lim["max"] - len(used)
        if candidates and len(candidates) > left:
            due = min((c.deadline_date for c in candidates if c.deadline_date), default=None)
            msg = (f"{inst['name']}: {lim['note']} Used {len(used)} of {lim['max']}; {len(candidates)} open candidates "
                   f"for {max(left, 0)} remaining place(s). Choose which to apply to.")
            decisions.append(msg + " Candidates: " + "; ".join(f"{c.title} ({c.score})" for c in
                                                                 sorted(candidates, key=lambda c: -(c.score or 0))))
            for c in candidates:
                per_position[c.id] = FlagSpec("application_limit", msg, "decide", due)
    return per_position, decisions


def run_checks(s: Session, today: date | None = None) -> CheckReport:
    today = today or date.today()
    positions = list(s.scalars(select(Position)))
    applications = list(s.scalars(select(Application)))
    insights = list(s.scalars(select(Insight)))
    limits, decisions = limit_flags(positions, applications, today)

    existing = {(f.position_id, f.kind): f for f in s.scalars(select(Flag)) if f.kind in ENGINE_KINDS}
    seen = set()
    report = CheckReport(decisions=decisions)
    for p in positions:
        specs = position_flags(p, insights, today)
        if p.id in limits:
            specs.append(limits[p.id])
        for spec in specs:
            key = (p.id, spec.kind)
            seen.add(key)
            f = existing.get(key)
            if f is None:
                f = Flag(position_id=p.id, kind=spec.kind)
                s.add(f)
            if f.status == "resolved" and f.message == spec.message:
                continue            # Farbod already dealt with this exact flag
            f.message, f.action, f.due_on, f.status = spec.message, spec.action, spec.due_on, "open"
            report.flags_by_action[spec.action] = report.flags_by_action.get(spec.action, 0) + 1
    # conditions that no longer hold resolve themselves
    for key, f in existing.items():
        if key not in seen and f.status == "open":
            f.status = "resolved"
    return report


def seed_insights(s: Session, path=None) -> int:
    path = path or ROOT / "seed" / "insights.yaml"
    items = yaml.safe_load(open(path, encoding="utf-8"))
    have = {(i.scope_type, i.scope_value, i.text) for i in s.scalars(select(Insight))}
    n = 0
    for it in items:
        key = (it["scope_type"], it["scope_value"], it["text"])
        if key not in have:
            s.add(Insight(**it))
            n += 1
    return n
