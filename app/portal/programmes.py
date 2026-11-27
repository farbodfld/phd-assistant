"""Doctoral programmes with a cap on projects per applicant (knowledge/programmes.yaml)."""
from datetime import date
from functools import lru_cache

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Position
from app.knowledge.loader import KNOWLEDGE


@lru_cache
def load_programmes() -> list[dict]:
    path = KNOWLEDGE / "programmes.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or [] if path.exists() else []


def _text(p: Position) -> str:
    return f"{p.title or ''} {p.university or ''}".lower()


def programme_of(p: Position) -> dict | None:
    t = _text(p)
    for prog in load_programmes():
        if any(m in t for m in prog.get("match", [])) and all(a in t for a in prog.get("also", [])):
            return prog
    return None


def members(s: Session, prog: dict) -> list[Position]:
    pats = prog.get("match", [])
    out = []
    for p in s.scalars(select(Position)):
        t = _text(p)
        if any(m in t for m in pats) and all(a in t for a in prog.get("also", [])):
            out.append(p)
    return out


def submitted_in(s: Session, prog: dict) -> list[Position]:
    return [p for p in members(s, prog) if p.portal_done or p.portal_submitted]


def cap_status(s: Session, p: Position) -> dict | None:
    """The programme this position belongs to, how many are submitted and whether the cap is reached."""
    prog = programme_of(p)
    if not prog:
        return None
    done = submitted_in(s, prog)
    return {"name": prog["name"], "max": prog["max"], "note": prog.get("note", ""),
            "submitted": [x.id for x in done], "full": len(done) >= prog["max"]}


def close_capped(s: Session, p: Position, today: date) -> list[int]:
    """After a submission: when the programme's cap is reached, close its other open positions."""
    prog = programme_of(p)
    if not prog or len(submitted_in(s, prog)) < prog["max"]:
        return []
    closed = []
    for x in members(s, prog):
        if x.closed or x.portal_done or x.portal_submitted:
            continue
        x.closed = True
        x.add_status(f"Closed: {prog['name']} cap of {prog['max']} projects reached.", on=today)
        closed.append(x.id)
    return closed
