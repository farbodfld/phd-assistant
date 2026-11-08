"""The tracker's exact layout and colours, shared by importer and exporter."""
from dataclasses import dataclass

SHEET_FINDAPHD = "PhD Matches (FindAPhD)"
SHEET_JOBSAC = "jobs.ac.uk"
SHEET_SUMMARY = "Summary"
SHEET_ABOUT = "About this search"
EXCLUDED_HEADER = "Checked and excluded"

HEADER_FILL = "1F4E78"
GREY = "D9D9D9"
LINK_BLUE = "0563C1"
LINK_TEXT = "Open listing"
TICK = "✓"

APPLY_FILLS = {"Email": "DDEBF7", "Portal": "FCE4D6", "Email + Portal": "E2EFDA"}


def score_fill(score: int | None) -> str | None:
    if score is None:
        return None
    if score >= 70:
        return "63BE7B"
    if score >= 50:
        return "FFEB84"
    if score >= 35:
        return "F8B26A"
    return "F8696B"


@dataclass(frozen=True)
class Col:
    header: str
    field: str        # Position attribute
    width: float
    bold: bool = False


_COMMON_HEAD = [
    Col("Rank", "rank", 7, bold=True),
    Col("Score", "score", 8),
    Col("PhD Position", "title", 42),
    Col("University", "university", 24),
]
_COMMON_MID = [
    Col("Deadline", "deadline_text", 15),
    Col("Apply Method", "apply_method", 16, bold=True),
    Col("Email", "email_done", 8),
    Col("Portal", "portal_done", 8),
    Col("Email Sent", "email_sent", 13),
    Col("Reminder 1", "reminder_1", 14),
    Col("Reminder 2", "reminder_2", 12),
    Col("Portal Submitted", "portal_submitted", 14),
]

FINDAPHD_COLS = _COMMON_HEAD + [Col("Supervisor / Contact", "supervisor_contact", 30)] + _COMMON_MID + [
    Col("Status / Next Action", "status", 60),
    Col("Why it fits", "fit_note", 45),
    Col("Link", "link", 14),
]

JOBSAC_COLS = _COMMON_HEAD + [Col("Supervisor / Contact", "supervisor_contact", 26)] + _COMMON_MID + [
    Col("Status / Next Action", "status", 45),
    Col("Funding (international)", "fit_note", 45),
    Col("Application Route", "application_route", 32),
    Col("Type", "type", 20),
    Col("Link", "link", 14),
]

COLS_BY_SOURCE = {"findaphd": FINDAPHD_COLS, "jobsac": JOBSAC_COLS}
SHEET_BY_SOURCE = {"findaphd": SHEET_FINDAPHD, "jobsac": SHEET_JOBSAC}
