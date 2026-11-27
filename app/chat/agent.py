"""The Telegram assistant: Opus with tools over the project's own data.

It answers questions about positions, emails, drafts and tasks, and can do safe things for Farbod
(draft a first email, edit a draft, write a statement or proposal for an email, prepare a portal pack, record a supervisor's
address, close a position, tick a task). It never sends an email: a draft is shown with its Approve button.
"""
import json
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select

from app.config import get_settings
from app.db.models import (Contact, Draft, EmailMessage, LLMCall, Meta, Position, UserTask)
from app.knowledge.loader import build_context

HISTORY_KEY = "chat:history"
MAX_TURNS = 16
MAX_STEPS = 8

SYSTEM = """You are Farbod's PhD-search assistant, talking with him in Telegram. He writes in Persian (sometimes
English). Answer in Persian, keep English technical terms, names, titles and emails in English, and be brief:
short paragraphs or a few bullet lines, no markdown tables, no headings.

Use the tools for every fact about positions, emails, drafts, tasks and costs; never guess. Quote position and
draft numbers (#27) so he can refer to them. You may draft, edit and prepare, but you never send an email and
never claim something was sent: to send, show the draft (show_draft) and tell him to press Approve under it.
Never enter passwords, log in, pay or submit anything. Follow the SOPs and use only verified profile facts.
When he asks for a change to a draft, call edit_draft. When unsure which position or draft he means, ask."""


def _tool(name, description, props=None, required=()):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props or {}, "required": list(required)}}


INT = {"type": "integer"}
STR = {"type": "string"}
TOOLS = [
    _tool("search_positions", "Find tracker positions. Returns id, title, university, score, deadline, apply method, "
          "whether he emailed, and the latest status line.",
          {"query": {**STR, "description": "words in the title, university or supervisor"},
           "status": {"type": "string", "enum": ["open", "closed", "all"]},
           "apply_method": {"type": "string", "enum": ["Email", "Portal", "Email + Portal"]},
           "min_score": INT, "deadline_within_days": INT, "not_emailed_only": {"type": "boolean"},
           "limit": INT}),
    _tool("get_position", "Everything about one position: fields, contacts, status history, emails, drafts.",
          {"position_id": INT}, ["position_id"]),
    _tool("search_emails", "Search stored emails (supervisors' replies and his own) by words, sender or subject.",
          {"query": STR, "limit": INT}, ["query"]),
    _tool("list_drafts", "Drafts by status (default pending).", {"status": STR}),
    _tool("get_draft", "One draft in full.", {"draft_id": INT}, ["draft_id"]),
    _tool("list_tasks", "Open tasks only Farbod can do."),
    _tool("stats", "Counts and today's model spend against the daily budget."),
    _tool("show_draft", "Show a draft to Farbod in Telegram with its Approve / Edit / Deny buttons.",
          {"draft_id": INT}, ["draft_id"]),
    _tool("edit_draft", "Rewrite a pending draft following an instruction; the new version is shown to him.",
          {"draft_id": INT, "instruction": STR}, ["draft_id", "instruction"]),
    _tool("draft_first_email", "Write the first email to a position's supervisor (a draft; shown to him).",
          {"position_id": INT}, ["position_id"]),
    _tool("write_document", "Write a statement or proposal (Word and PDF) into the position folder.",
          {"position_id": INT, "kind": {"type": "string", "enum": ["statement", "proposal"]}},
          ["position_id", "kind"]),
    _tool("set_supervisor_email", "Record the supervisor's email address he found, so a first email can be drafted.",
          {"position_id": INT, "email": STR, "name": STR}, ["position_id", "email"]),
    _tool("set_apply_method", "Change how a position is applied to.",
          {"position_id": INT, "apply_method": {"type": "string", "enum": ["Email", "Portal", "Email + Portal"]}},
          ["position_id", "apply_method"]),
    _tool("close_position", "Close (grey out) a position, with the reason.", {"position_id": INT, "reason": STR},
          ["position_id", "reason"]),
    _tool("add_note", "Add a dated note to a position's status.", {"position_id": INT, "note": STR},
          ["position_id", "note"]),
    _tool("mark_task_done", "Tick off one of his tasks.", {"task_id": INT}, ["task_id"]),
    _tool("list_portals", "Portal applications: packs ready (not yet submitted) and positions still without a pack."),
    _tool("prepare_portal_pack", "Build the portal pack for a position: the facts his Portal session needs (deadline, "
          "programme cap, rules, contact history, files ready, referees). No model calls; documents are written in "
          "the Portal session, not here. The pack card is shown to him afterwards.",
          {"position_id": INT}, ["position_id"]),
    _tool("mark_portal_submitted", "Record that he submitted the portal application himself (only when he says so).",
          {"position_id": INT, "reference": STR}, ["position_id"]),
    _tool("skip_portal", "He does not want to apply to this portal: no pack and no deadline nudges.",
          {"position_id": INT}, ["position_id"]),
]


def _status_head(p: Position) -> str:
    return (p.status or "").split(" | ")[0][:160]


def _pos_line(p: Position) -> dict:
    return {"id": p.id, "title": p.title, "university": p.university, "score": p.score,
            "deadline": p.deadline_date.isoformat() if p.deadline_date else p.deadline_text,
            "apply_method": p.apply_method, "emailed": bool(p.email_done), "closed": p.closed,
            "supervisor": p.supervisor_contact, "latest_status": _status_head(p)}


def _draft_line(d: Draft) -> dict:
    return {"id": d.id, "kind": d.kind, "status": d.status, "to": d.to, "subject": d.subject,
            "position_id": d.position_id, "version": d.version}


class Agent:
    """ui: the bot (show_draft, apply_edit). llm: LLMClient. Every tool runs inside the caller's session."""

    def __init__(self, llm, ui, today: date):
        self.llm, self.ui, self.today = llm, ui, today
        self.shown: list[int] = []
        self.packs: list[int] = []

    # ----------------------------------------------------------------- tools
    def run_tool(self, s, name: str, a: dict):
        if name == "search_positions":
            q = select(Position)
            status = a.get("status", "open")
            if status == "open":
                q = q.where(Position.closed.is_(False))
            elif status == "closed":
                q = q.where(Position.closed.is_(True))
            if a.get("query"):
                like = f"%{a['query']}%"
                q = q.where(or_(Position.title.ilike(like), Position.university.ilike(like),
                                Position.supervisor_contact.ilike(like)))
            if a.get("apply_method"):
                q = q.where(Position.apply_method == a["apply_method"])
            if a.get("min_score") is not None:
                q = q.where(Position.score >= a["min_score"])
            if a.get("deadline_within_days") is not None:
                q = q.where(Position.deadline_date >= self.today,
                            Position.deadline_date <= self.today + timedelta(days=a["deadline_within_days"]))
            if a.get("not_emailed_only"):
                q = q.where(Position.email_done.is_(False))
            rows = list(s.scalars(q.order_by(Position.deadline_date.is_(None), Position.deadline_date,
                                             Position.score.desc()).limit(min(a.get("limit") or 20, 40))))
            return [_pos_line(p) for p in rows]
        if name == "get_position":
            p = s.get(Position, a["position_id"])
            if not p:
                return {"error": "no such position"}
            mails = s.scalars(select(EmailMessage).where(EmailMessage.position_id == p.id)
                              .order_by(EmailMessage.sent_at.desc()).limit(10))
            drafts = s.scalars(select(Draft).where(Draft.position_id == p.id).order_by(Draft.id.desc()).limit(10))
            return {**_pos_line(p), "link": p.link, "funding": p.funding, "fit_note": p.fit_note,
                    "status_history": (p.status or "")[:1500],
                    "contacts": [{"name": c.name, "email": c.email} for c in p.contacts],
                    "emails": [{"date": m.sent_at.date().isoformat(), "direction": m.direction, "from": m.sender,
                                "subject": m.subject, "category": m.category, "summary": m.summary} for m in mails],
                    "drafts": [_draft_line(d) for d in drafts]}
        if name == "search_emails":
            like = f"%{a['query']}%"
            rows = s.scalars(select(EmailMessage).where(or_(
                EmailMessage.subject.ilike(like), EmailMessage.sender.ilike(like), EmailMessage.summary.ilike(like),
                EmailMessage.body.ilike(like))).order_by(EmailMessage.sent_at.desc()).limit(min(a.get("limit") or 10, 20)))
            return [{"date": m.sent_at.date().isoformat(), "direction": m.direction, "from": m.sender,
                     "subject": m.subject, "position_id": m.position_id, "summary": m.summary,
                     "body": (m.body or "")[:600]} for m in rows]
        if name == "list_drafts":
            rows = s.scalars(select(Draft).where(Draft.status == a.get("status", "pending")).order_by(Draft.id))
            return [_draft_line(d) for d in rows]
        if name == "get_draft":
            d = s.get(Draft, a["draft_id"])
            return {**_draft_line(d), "body": d.body, "attachments": d.attachments, "notes": d.notes,
                    "issues": d.issues} if d else {"error": "no such draft"}
        if name == "list_tasks":
            rows = s.scalars(select(UserTask).where(UserTask.done.is_(False)).order_by(UserTask.due_on))
            return [{"id": t.id, "text": t.text, "due": t.due_on.isoformat() if t.due_on else None,
                     "position_id": t.position_id} for t in rows]
        if name == "stats":
            start = datetime.combine(self.today, datetime.min.time())
            return {"open_positions": s.scalar(select(func.count(Position.id)).where(Position.closed.is_(False))),
                    "pending_drafts": s.scalar(select(func.count(Draft.id)).where(Draft.status == "pending")),
                    "sent_emails": s.scalar(select(func.count(Draft.id)).where(Draft.status == "sent")),
                    "open_tasks": s.scalar(select(func.count(UserTask.id)).where(UserTask.done.is_(False))),
                    "spent_today_usd": round(s.scalar(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0))
                                                      .where(LLMCall.created_at >= start)), 3),
                    "daily_budget_usd": get_settings().daily_budget_usd}
        if name == "show_draft":
            d = s.get(Draft, a["draft_id"])
            if not d:
                return {"error": "no such draft"}
            self.shown.append(d.id)
            return {"ok": True, "note": "it will be shown after your answer"}
        if name == "edit_draft":
            d = s.get(Draft, a["draft_id"])
            if not d or d.status not in ("pending", "approved"):
                return {"error": "no pending draft with that number"}
            self.ui.apply_edit(s, d, a["instruction"])
            return {"ok": True, "version": d.version, "note": "the new version was shown to him with its buttons"}
        if name == "draft_first_email":
            from app.outreach.initial import draft_initial
            p = s.get(Position, a["position_id"])
            if not p:
                return {"error": "no such position"}
            m = s.get(Meta, f"outreach:{p.id}")
            if m:
                s.delete(m)
            d = draft_initial(s, p, self.llm, self.today)
            s.commit()
            if d is None:
                m = s.get(Meta, f"outreach:{p.id}")
                return {"error": f"no draft: {m.value if m else 'see tasks'}"}
            self.shown.append(d.id)
            return {"ok": True, "draft_id": d.id, "to": d.to, "issues": d.issues}
        if name == "write_document":
            from app.outreach.advert import advert_text
            from app.outreach.documents import write_document
            p = s.get(Position, a["position_id"])
            if not p:
                return {"error": "no such position"}
            w = write_document(s, p, a["kind"], self.llm, advert_text(p))
            s.commit()
            return {"ok": True, "pdf": w.pdf.name, "docx": w.docx.name, "folder": p.folder_name,
                    "reference_only": w.reference_only, "notes": w.notes_fa, "issues": w.issues}
        if name == "set_supervisor_email":
            p = s.get(Position, a["position_id"])
            if not p:
                return {"error": "no such position"}
            email = a["email"].strip().lower()
            if not any((c.email or "").lower() == email for c in p.contacts):
                p.contacts.append(Contact(name=a.get("name") or email, email=email))
            m = s.get(Meta, f"outreach:{p.id}")
            if m and str(m.value).startswith("no supervisor email"):
                s.delete(m)
            for t in s.scalars(select(UserTask).where(UserTask.position_id == p.id, UserTask.done.is_(False))):
                if "آدرس" in t.text:
                    t.done = True
            p.add_status(f"Supervisor email recorded: {email}.", on=self.today)
            s.commit()
            return {"ok": True, "note": "draft_first_email can now write to this address"}
        if name in ("set_apply_method", "close_position", "add_note"):
            p = s.get(Position, a["position_id"])
            if not p:
                return {"error": "no such position"}
            if name == "set_apply_method":
                p.apply_method = a["apply_method"]
                p.add_status(f"Apply method set to {a['apply_method']} by Farbod.", on=self.today)
            elif name == "close_position":
                p.closed = True
                p.add_status(f"CLOSED: {a['reason']} (Farbod)", on=self.today)
            else:
                p.add_status(a["note"], on=self.today)
            s.commit()
            return {"ok": True}
        if name == "mark_task_done":
            t = s.get(UserTask, a["task_id"])
            if not t:
                return {"error": "no such task"}
            t.done = True
            s.commit()
            return {"ok": True}
        if name == "list_portals":
            from app.portal.queue import candidates, packed
            return {"packs_ready": [_pos_line(p) for p in packed(s, self.today)],
                    "without_pack": [_pos_line(p) for p in candidates(s, self.today)[:25]]}
        if name in ("prepare_portal_pack", "mark_portal_submitted", "skip_portal"):
            p = s.get(Position, a["position_id"])
            if not p:
                return {"error": "no such position"}
            if name == "prepare_portal_pack":
                from app.portal.run import prepare
                pack = prepare(s, p, self.today)
                s.commit()
                self.packs.append(p.id)
                return {"ok": True, "pack": pack.path.name, "issues": pack.issues,
                        "note": f"shown to him after your answer; he starts it in the Portal session with 'پورتال #{p.id}'"}
            from app.portal.queue import mark_submitted, skip
            closed = []
            if name == "mark_portal_submitted":
                closed = mark_submitted(s, p, self.today, a.get("reference"))
            else:
                skip(s, p, self.today)
            s.commit()
            return {"ok": True, **({"closed_by_programme_cap": closed} if closed else {})}
        return {"error": f"unknown tool {name}"}

    # ----------------------------------------------------------------- conversation
    def ask(self, s, text: str, about_draft: int | None = None) -> str:
        history = list((s.get(Meta, HISTORY_KEY) or Meta(value=[])).value or [])
        user = text if about_draft is None else f"[He is replying to draft #{about_draft}]\n{text}"
        messages = history + [{"role": "user", "content": user}]
        system = [{"type": "text", "text": c} for c in build_context("chat")]
        if system:
            system[-1]["cache_control"] = {"type": "ephemeral"}
        system.append({"type": "text", "text": SYSTEM + f"\nToday is {self.today.isoformat()}."})
        answer = ""
        for _ in range(MAX_STEPS):
            resp = self.llm.converse("chat", messages, system=system, tools=TOOLS)
            messages.append({"role": "assistant", "content": resp.content})
            uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
            answer = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            if not uses:
                break
            results = []
            for b in uses:
                try:
                    out = self.run_tool(s, b.name, dict(b.input or {}))
                except Exception as exc:
                    s.rollback()
                    out = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out, ensure_ascii=False, default=str)[:8000]})
            messages.append({"role": "user", "content": results})
        answer = answer or "انجام شد."
        turns = (history + [{"role": "user", "content": user}, {"role": "assistant", "content": answer}])[-MAX_TURNS:]
        s.merge(Meta(key=HISTORY_KEY, value=turns))
        s.commit()
        return answer


def reset(s):
    m = s.get(Meta, HISTORY_KEY)
    if m:
        s.delete(m)
        s.commit()
