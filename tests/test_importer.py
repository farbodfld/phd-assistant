from pathlib import Path

from sqlalchemy import select

from app.db.base import session_scope
from app.db.models import Draft, Notification
from app.outreach.importer import import_drafts
from tests.test_outreach import TODAY, _pos, folders  # noqa: F401


def test_import_attaches_files_writes_documents_and_needs_no_model(db, folders):
    for name in ("MSc_HEAR_Transcript_under2MB.pdf", "BSc Transcript.pdf"):
        (folders / "documents" / name).write_bytes(b"%PDF")
    item = {"position_id": 1, "to": "J.Smith@leeds.ac.uk", "name": "Dr J Smith", "subject": "PhD enquiry: Robots",
            "body": "Dear Dr Smith,\n\nPlease find attached my CV, transcripts and covering letter - thanks.\n\n"
                    "Best regards,\nFarbod Fooladi",
            "transcripts": True,
            "documents": [{"kind": "statement", "title": "Covering letter",
                           "sections": [{"heading": "", "paragraphs": ["I am applying - with enthusiasm."]}]}],
            "notes": ["یادداشت"]}
    with session_scope() as s:
        p = _pos(s)
        s.commit()
        r = import_drafts(s, [item, item], TODAY)
        d = s.scalars(select(Draft)).one()
        assert r.created == [d.id] and "already exists" in r.skipped[0]
        names = [Path(a).name for a in d.attachments]
        assert names == ["Farbod_Fooladi_CV.pdf", "MSc_HEAR_Transcript_under2MB.pdf", "BSc Transcript.pdf",
                         "Farbod_Fooladi_Personal_Statement_v1.pdf"]
        assert d.to == "j.smith@leeds.ac.uk" and " - " not in d.body and not d.issues
        assert (folders / "positions" / p.folder_name / "Farbod_Fooladi_Personal_Statement_v1.docx").exists()
        assert s.scalars(select(Notification.kind).where(Notification.kind == "review")).one()
