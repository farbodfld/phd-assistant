"""A portal pack: everything the Portal session needs to apply for one position, with no model calls.

The agent no longer writes portal documents or fills portals. It gathers facts: the position, the deadline,
programme caps, institution rules, contact history, the files ready on disk and the referees. Farbod's
Portal session (Claude in Chrome, his subscription) reads the pack, writes any documents on the university's
own templates, fills the portal and stops before Submit. Files: portal_pack.json (exact, for the session) and
Farbod_Fooladi_Portal_Pack.md (to read) in the position folder.
"""
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Draft, EmailMessage, Position
from app.knowledge.loader import institutions_for, load_profile
from app.outreach.documents import _record, ai_writing_banned, cv_for, folder_for, transcripts_for
from app.portal.programmes import cap_status
from app.portal.queue import set_state

PACK_NAME = "Farbod_Fooladi_Portal_Pack.md"
JSON_NAME = "portal_pack.json"
RULE_KEYS = ("ai_writing", "application_limit", "english", "funding_note", "funding_ambiguity", "entry_requirement",
             "process_note", "portal_notes")
SESSION_STEPS = [
    "Read the advert and its How to apply section; note every required document, length and file naming rule.",
    "Look for the university's own templates (proposal, personal statement, training needs, CV) and use them.",
    "Write the documents in the Portal session from profile.yaml facts only; show each one to Farbod first.",
    "Fill the portal in Farbod's Chrome page by page; upload files from the documents and position folders.",
    "Stop before Submit. Farbod handles login, passport, visa, immigration questions and declarations.",
    "After Submit: record it with /done <id> <reference> in Telegram (or `phd portal-done`).",
]
LEFT_FOR_FARBOD = ["Log in or create the account (password)", "Passport number and upload, visa uploads",
                   "Immigration questions and declarations", "Any fee", "Submit"]


@dataclass
class Pack:
    position_id: int
    path: Path                     # the Markdown pack
    json_path: Path
    data: dict
    issues: list[str] = field(default_factory=list)
    drive_folder: str | None = None


def _history(s: Session, p: Position) -> list[dict]:
    out = []
    for e in s.scalars(select(EmailMessage).where(EmailMessage.position_id == p.id).order_by(EmailMessage.sent_at)):
        out.append({"date": e.sent_at.date().isoformat() if e.sent_at else "", "direction": e.direction,
                    "who": e.sender if e.direction == "in" else (e.recipients or ""),
                    "category": e.category or "", "about": (e.summary or e.subject or "")[:240]})
    if not any(h["direction"] == "out" for h in out):
        for d in s.scalars(select(Draft).where(Draft.position_id == p.id, Draft.status == "sent").order_by(Draft.created_at)):
            out.append({"date": d.created_at.date().isoformat() if d.created_at else "", "direction": "out",
                        "who": d.to, "category": d.kind, "about": d.subject or ""})
        out.sort(key=lambda h: h["date"])
    return out


def _rules(p: Position) -> list[dict]:
    out = []
    for inst in institutions_for(f"{p.university} {p.title}"):
        r = {k: inst[k] for k in RULE_KEYS if inst.get(k)}
        if r:
            out.append({"institution": inst["name"], **r})
    return out


def _english(p: Position, profile: dict) -> str:
    for inst in institutions_for(p.university):
        if inst.get("english"):
            e = inst["english"]
            return f"No English test. {e if isinstance(e, str) else e.get('note', '')}".strip()
    return ("No English test. Use the MSc Data Science, University of Essex (UK, taught in English, Oct 2024 to "
            "Oct 2025) and upload the MSc certificate as evidence.")


ROOT = Path(__file__).resolve().parents[2]


def _rel(path) -> str:
    """Paths relative to the project folder, so they read the same on the laptop, the server and Drive."""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def pack_data(s: Session, p: Position, today: date) -> tuple[dict, list[str]]:
    profile = load_profile()
    issues = []
    cv = cv_for(s, p)
    if not cv:
        issues.append("master CV not found in the documents folder")
    tr = transcripts_for(s, p)
    if not tr:
        issues.append("transcripts not found in the documents folder")
    folder = folder_for(p)
    docs_dir = Path(get_settings().documents_dir)
    days = (p.deadline_date - today).days if p.deadline_date else None
    data = {
        "id": p.id, "title": p.title, "university": p.university, "supervisor": p.supervisor_contact,
        "deadline": {"text": p.deadline_text, "date": p.deadline_date.isoformat() if p.deadline_date else None,
                     "days_left": days},
        "score": p.score, "fit_note": p.fit_note, "funding": p.funding, "advert_url": p.link,
        "apply_method": p.apply_method, "application_route": p.application_route, "source": p.source,
        "programme_cap": cap_status(s, p),
        "ai_writing_banned": ai_writing_banned(p),
        "institution_rules": _rules(p),
        "contact_history": _history(s, p),
        "files": {"position_folder": _rel(folder), "cv": _rel(cv) if cv else None, "transcripts": [_rel(x) for x in tr],
                  "documents_folder": _rel(docs_dir),
                  "documents_available": sorted(x.name for x in docs_dir.glob("*") if x.is_file()
                                                and "backup" not in x.name.lower()) if docs_dir.exists() else [],
                  "in_position_folder": sorted(x.name for x in folder.glob("*") if x.is_file() and x.name not in (PACK_NAME, JSON_NAME))},
        "referees": [{k: r.get(k) for k in ("title", "name", "position", "institution", "email")}
                     for r in profile.get("referees", [])],
        "english": _english(p, profile),
        "fee_status": "International (overseas); applying for the funded studentship only, not self funded.",
        "session_steps": SESSION_STEPS,
        "left_for_farbod": LEFT_FOR_FARBOD,
        "generated_on": today.isoformat(),
    }
    return data, issues


def render_md(d: dict, issues: list[str]) -> str:
    dl = d["deadline"]
    when = dl["date"] or "rolling"
    if dl["days_left"] is not None:
        when += f" ({dl['days_left']} days left)"
    lines = [f"# Portal pack #{d['id']}: {d['title']}", "",
             f"- University: {d['university']}", f"- Supervisor: {d['supervisor'] or 'see the advert'}",
             f"- Deadline: {when}" + (f". Advert says: {dl['text']}" if dl["text"] else ""),
             f"- Score: {d['score']}" + (f". Fit: {d['fit_note']}" if d["fit_note"] else ""),
             f"- Funding: {d['funding'] or 'see the advert'}", f"- Advert: {d['advert_url'] or ''}",
             f"- Apply method: {d['apply_method'] or ''}" + (f" ({d['application_route']})" if d["application_route"] else ""),
             f"- Fee status: {d['fee_status']}", ""]
    cap = d["programme_cap"]
    if cap:
        lines += ["## Programme cap", f"- {cap['name']}: max {cap['max']}. {cap['note']}",
                  f"- Already submitted in this programme: {', '.join('#' + str(i) for i in cap['submitted']) or 'none'}"
                  + (" (CAP REACHED: do not apply)" if cap["full"] else ""), ""]
    if d["ai_writing_banned"]:
        lines += ["## AI writing", "- This university does not accept AI-written statements or proposals: give Farbod "
                  "a reference draft only; he writes the final text.", ""]
    if d["institution_rules"]:
        lines.append("## Institution rules")
        for r in d["institution_rules"]:
            for k, v in r.items():
                if k == "institution":
                    continue
                vals = v if isinstance(v, list) else [v]
                lines += [f"- {r['institution']}, {k.replace('_', ' ')}: {x if not isinstance(x, dict) else x.get('note', x)}" for x in vals]
        lines.append("")
    lines.append("## Contact history")
    lines += [f"- {h['date']} {'<-' if h['direction'] == 'in' else '->'} {h['who']} [{h['category']}] {h['about']}"
              for h in d["contact_history"]] or ["- none"]
    f = d["files"]
    lines += ["", "## Files", f"- Position folder: {f['position_folder']}", f"- CV: {f['cv'] or 'MISSING'}",
              f"- Transcripts: {', '.join(Path(x).name for x in f['transcripts']) or 'MISSING'}",
              f"- In the documents folder: {', '.join(f['documents_available']) or 'nothing'}",
              f"- Already in the position folder: {', '.join(f['in_position_folder']) or 'nothing'}", "",
              "## Referees (ask Farbod which ones)"]
    lines += [f"- {r['title']} {r['name']}, {r['position']}, {r['institution']}, {r['email']}" for r in d["referees"]]
    lines += ["", "## English", f"- {d['english']}", "", "## Portal session steps"]
    lines += [f"{i}. {x}" for i, x in enumerate(d["session_steps"], 1)]
    lines += ["", "## Left for Farbod"] + [f"- {x}" for x in d["left_for_farbod"]]
    if issues:
        lines += ["", "## Issues"] + [f"- {x}" for x in issues]
    return "\n".join(lines) + "\n"


def build_pack(s: Session, p: Position, today: date) -> Pack:
    data, issues = pack_data(s, p, today)
    folder = folder_for(p)
    md, js = folder / PACK_NAME, folder / JSON_NAME
    md.write_text(render_md(data, issues), encoding="utf-8")
    js.write_text(json.dumps({**data, "issues": issues}, ensure_ascii=False, indent=2), encoding="utf-8")
    _record(s, p, md, "portal_pack")
    set_state(s, p.id, {"status": "packed", "on": today.isoformat(), "pack": str(md), "json": str(js),
                        "issues": issues})
    p.add_status("Portal pack prepared for the Portal session.", on=today)
    return Pack(p.id, md, js, data, issues)
