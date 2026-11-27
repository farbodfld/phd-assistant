"""What was actually submitted through a portal: files the Portal session wrote, and its manifest.

The Portal session puts the final files and submitted.json in a `submitted` folder next to the pack:
on the laptop, <position folder>/submitted/; on the server, the position's Google Drive folder/submitted/.
When Farbod records the submission (/done or `phd portal-done`), the files are copied in (from Drive when
DRIVE_PACKS is on), linked to the position in the documents table, and summarised in its status.

submitted.json (all optional):
{"submitted_on": "2026-11-27", "reference": "ABC123", "portal_url": "...",
 "uploaded": [{"file": "Research_Proposal.pdf", "kind": "proposal", "note": "on the university template"},
              {"file": "Farbod_Fooladi_CV.pdf", "kind": "cv"}],
 "referees": ["Behnam Gholami", "Danial ..."],
 "answers": {"funding": "RDS studentship", "projects": ["..."]},
 "notes": "..."}
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Document, Position
from app.outreach.documents import folder_for
from app.portal.queue import mark_submitted, set_state, state

log = logging.getLogger("portal")
SUBMITTED_DIR = "submitted"
MANIFEST = "submitted.json"
KINDS = {"proposal": "proposal", "statement": "statement", "personal_statement": "statement", "cv": "cv",
         "transcript": "transcript", "certificate": "certificate", "training_needs": "other", "cover_letter": "other"}


@dataclass
class Submission:
    position_id: int
    files: list[str] = field(default_factory=list)          # newly recorded files (names)
    manifest: dict = field(default_factory=dict)
    closed: list[int] = field(default_factory=list)          # closed by a programme cap
    issues: list[str] = field(default_factory=list)


def _guess_kind(name: str) -> str:
    n = name.lower()
    for key, kind in (("proposal", "proposal"), ("statement", "statement"), ("cv", "cv"), ("transcript", "transcript"),
                      ("certificate", "certificate")):
        if key in n:
            return kind
    return "other"


def submitted_dir(p: Position) -> Path:
    return folder_for(p) / SUBMITTED_DIR


def ingest(s: Session, p: Position, today: date, drive=None) -> Submission:
    """Bring the submitted files in (Drive first when it is on), record new ones, keep the manifest."""
    sub = Submission(p.id)
    local = submitted_dir(p)
    if get_settings().drive_packs:
        try:
            from app.portal.drive import DrivePacks
            got = (drive or DrivePacks()).download_submitted(p.id, p.title, p.university, local)
            log.info("downloaded %d submitted files for #%s", len(got), p.id)
        except Exception as exc:
            sub.issues.append(f"Drive download failed: {type(exc).__name__}: {str(exc)[:120]}")
    if not local.exists():
        return sub
    mf = local / MANIFEST
    if mf.exists():
        try:
            sub.manifest = json.loads(mf.read_text(encoding="utf-8"))
        except ValueError as exc:
            sub.issues.append(f"submitted.json unreadable: {exc}")
    kinds = {u.get("file"): KINDS.get(u.get("kind", ""), "other") for u in sub.manifest.get("uploaded", [])
             if isinstance(u, dict) and u.get("file")}
    known = set(s.scalars(select(Document.filename).where(Document.position_id == p.id)))
    for f in sorted(local.iterdir()):
        if not f.is_file() or f.name == MANIFEST:
            continue
        name = f"{SUBMITTED_DIR}/{f.name}"
        if name in known:
            continue
        s.add(Document(position_id=p.id, folder_name=p.folder_name, filename=name,
                       kind=kinds.get(f.name) or _guess_kind(f.name), version=1, is_final=True,
                       size_bytes=f.stat().st_size, modified_at=datetime.fromtimestamp(f.stat().st_mtime)))
        sub.files.append(f.name)
    st = state(s, p.id) or {}
    set_state(s, p.id, {**st, "submitted_files": sorted(set(st.get("submitted_files", [])) | set(sub.files)),
                        **({"manifest": sub.manifest} if sub.manifest else {})})
    return sub


def summary(sub: Submission) -> str:
    m = sub.manifest
    parts = []
    uploaded = [u.get("file") for u in m.get("uploaded", []) if isinstance(u, dict)]
    if uploaded or sub.files:
        parts.append("with " + ", ".join(dict.fromkeys(uploaded or sub.files)))
    if m.get("referees"):
        parts.append("referees " + ", ".join(m["referees"]))
    return "; ".join(parts)


def record_submission(s: Session, p: Position, today: date, reference: str | None = None, drive=None) -> Submission:
    """Mark the portal submitted, then bring in what was submitted. A missing submitted folder is fine."""
    sub = ingest(s, p, today, drive)
    ref = reference or sub.manifest.get("reference")
    sub.closed = mark_submitted(s, p, today, ref)
    line = summary(sub)
    if line:
        p.add_status(f"Submitted {line}.", on=today)
    return sub


def submission_info(s: Session, p: Position) -> dict:
    """For the chat assistant: what was submitted for this position, and where the files are."""
    st = state(s, p.id) or {}
    docs = s.scalars(select(Document).where(Document.position_id == p.id,
                                            Document.filename.like(f"{SUBMITTED_DIR}/%"))).all()
    return {"submitted_on": st.get("submitted_on"), "reference": st.get("reference"),
            "manifest": st.get("manifest") or {},
            "files": [{"file": d.filename, "kind": d.kind} for d in docs],
            "folder": str(submitted_dir(p))}
