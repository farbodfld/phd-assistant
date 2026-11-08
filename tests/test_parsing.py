from datetime import date

from app.tracker.layout import score_fill
from app.tracker.parsing import classify_document, parse_contacts, parse_deadline


def test_deadlines():
    assert parse_deadline("1 Dec 2026") == date(2026, 12, 1)
    assert parse_deadline("21 Oct 2026, 5pm") == date(2026, 10, 21)
    assert parse_deadline("2026-12-08") == date(2026, 12, 8)
    assert parse_deadline("Rolling") is None
    assert parse_deadline(None) is None


def test_contacts_assign_emails_by_name():
    assert parse_contacts("Dr Tao Chen (t.chen@bham.ac.uk)") == [("Dr Tao Chen", "t.chen@bham.ac.uk")]
    got = dict(parse_contacts("Prof D Prieto-Alhambra, Dr X Li (graduate.studies@ndorms.ox.ac.uk)"))
    assert got["Prof D Prieto-Alhambra"] is None          # generic inbox is not his address
    assert got["General contact (graduate.studies@ndorms.ox.ac.uk)"] == "graduate.studies@ndorms.ox.ac.uk"


def test_document_kinds():
    assert classify_document("Farbod_Fooladi_CV.pdf") == "cv"
    assert classify_document("MSc_HEAR_Transcript_under2MB.pdf") == "transcript"
    assert classify_document("Research_Proposal_Multimodal_World_Models.pdf") == "proposal"
    assert classify_document("Farbod_Fooladi_Letter_of_Application_Exeter_5895.pdf") == "statement"
    assert classify_document("Email_to_supervisor.txt") == "email_text"
    assert classify_document("~$ail_Draft_Dr_Tao_Chen.docx") == "temp"


def test_score_colours_match_tracker_key():
    assert score_fill(94) == "63BE7B"
    assert score_fill(70) == "63BE7B"
    assert score_fill(69) == "FFEB84"
    assert score_fill(50) == "FFEB84"
    assert score_fill(49) == "F8B26A"
    assert score_fill(35) == "F8B26A"
    assert score_fill(34) == "F8696B"
