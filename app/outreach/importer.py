"""Drafts written outside the daily run (in a working session with Claude) imported as ordinary drafts:
same attachments, checks, Telegram review and Gmail Drafts. No model call, so no cost.

File: a JSON list of
  {"position_id": 601, "to": "a@uni.ac.uk", "name": "Dr A", "subject": "...", "body": "...",
   "transcripts": true,
   "documents": [{"kind": "statement" | "proposal", "title": "...", "sections": [{"heading": "", "paragraphs": ["..."]}]}],
   "notes": ["Persian notes for Farbod"]}
"""
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Contact, Document, Draft, Notification, Position
from app.outreach.documents import (KINDS, DocText, _record, ai_writing_banned, cv_for, folder_for, master_cv,
                                    transcripts_for, write_files)
from app.outreach.initial import OPEN_DRAFT, _claimed_but_missing
from app.rules.text_checks import check_outgoing_text, fix_dash_connectors


@dataclass
class ImportReport:
    created: list[int] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def import_drafts(s: Session, items: list[dict], today: date) -> ImportReport:
    r = ImportReport()
    for it in items:
        p = s.get(Position, it["position_id"])
        if p is None:
            r.skipped.append(f"#{it['position_id']}: no such position")
            continue
        if p.closed or p.email_done:
            r.skipped.append(f"#{p.id}: closed or already emailed")
            continue
        if s.scalar(select(Draft.id).where(Draft.position_id == p.id, Draft.kind == "initial",
                                           Draft.status.in_(OPEN_DRAFT)).limit(1)):
            r.skipped.append(f"#{p.id}: a first-email draft already exists")
            continue
        notes, issues, attachments = list(it.get("notes", [])), [], []
        cv = cv_for(s, p)
        if cv:
            attachments.append(str(cv))
        else:
            issues.append(f"missing CV: {master_cv()} not found")
        if it.get("transcripts"):
            files = transcripts_for(s, p)
            if files:
                attachments += [str(f) for f in files]
            else:
                issues.append(f"missing transcripts: put them in {master_cv().parent}")
        for doc in it.get("documents", []):
            kind = doc["kind"]
            text = DocText(title=fix_dash_connectors(doc["title"]), sections=[
                {"heading": fix_dash_connectors(sec.get("heading", "")),
                 "paragraphs": [fix_dash_connectors(x) for x in sec["paragraphs"]]} for sec in doc["sections"]])
            reference = ai_writing_banned(p)
            version = (s.scalar(select(func.max(Document.version)).where(Document.position_id == p.id,
                                                                         Document.kind == kind)) or 0) + 1
            stem = KINDS[kind][1] + ("_REFERENCE_DRAFT" if reference else "")
            docx_path, pdf_path = write_files(text, folder_for(p) / f"Farbod_Fooladi_{stem}_v{version}", reference)
            for path in (docx_path, pdf_path):
                _record(s, p, path, kind, version, final=not reference)
            issues += [f"{i.kind}: {i.message} [{i.snippet}]" for i in check_outgoing_text(
                "\n".join(x for sec in text.sections for x in sec.paragraphs))]
            if reference:
                notes.insert(0, f"این دانشگاه نوشتن {kind} با AI رو ممنوع کرده؛ فقط reference draft ساخته شد و ضمیمه نشد.")
            else:
                attachments.append(str(pdf_path))
        body = fix_dash_connectors(it["body"])
        issues += [f"{i.kind}: {i.message} [{i.snippet}]" for i in check_outgoing_text(body, outreach=True)]
        issues += _claimed_but_missing(body, attachments)
        email = it["to"].strip().lower()
        if not any((c.email or "").lower() == email for c in p.contacts):
            p.contacts.append(Contact(name=it.get("name") or email, email=email))
        d = Draft(position_id=p.id, kind="initial", to=email, subject=fix_dash_connectors(it["subject"]), body=body,
                  attachments=attachments, notes=notes, issues=issues)
        s.add(d)
        s.flush()
        p.add_status(f"First email drafted to {email} (draft #{d.id}, written in a review session).", on=today)
        s.add(Notification(kind="email", ref_type="draft", ref_id=d.id,
                           text=f"✉️ ایمیل اولیه آماده‌ست: {p.title} ({p.university})"))
        s.commit()
        r.created.append(d.id)
    if r.created:
        s.add(Notification(kind="report", ref_type="pipeline",
                           text=f"✉️ {len(r.created)} ایمیل اولیه که با Claude نوشته شد اضافه شد."))
        s.add(Notification(kind="review", ref_type="pipeline", text=""))
        s.commit()
    return r


def load(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
