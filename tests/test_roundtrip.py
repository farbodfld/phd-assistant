"""Import the real tracker, export it again, and compare cell by cell.

Set TRACKER_XLSX to the tracker path (the file holds personal data, so it is
not stored in the repo). Skipped when the file is not available.
"""
import os
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from app.config import ROOT
from app.tracker.layout import (FINDAPHD_COLS, JOBSAC_COLS, SHEET_FINDAPHD,
                                SHEET_JOBSAC, score_fill)

XLSX = Path(os.environ.get("TRACKER_XLSX", "/mnt/user-data/uploads/PHD Apply/Farbod_PhD_Position_Matches.xlsx"))
pytestmark = pytest.mark.skipif(not XLSX.exists(), reason="tracker file not available")


def _rgb(cell):
    if not cell.fill or not cell.fill.fill_type:
        return None
    v = cell.fill.fgColor.rgb
    return v[-6:] if isinstance(v, str) else None


def _norm(v):
    if hasattr(v, "isoformat"):
        return v.date().isoformat() if hasattr(v, "date") else v.isoformat()
    return v


@pytest.fixture
def exported(db, tmp_path):
    from app.db.base import session_scope
    from app.tracker.exporter import export_workbook
    from app.tracker.importer import import_all
    with session_scope() as s:
        report = import_all(s, XLSX, ROOT / "seed" / "folder_inventory.json", ROOT / "seed" / "applications.yaml")
    with session_scope() as s:
        out = export_workbook(s, tmp_path / "out.xlsx", date(2026, 11, 8))
    return report, openpyxl.load_workbook(XLSX), openpyxl.load_workbook(out)


@pytest.mark.parametrize("sheet,cols", [(SHEET_FINDAPHD, FINDAPHD_COLS), (SHEET_JOBSAC, JOBSAC_COLS)])
def test_positions_survive_roundtrip(exported, sheet, cols):
    _, a, b = exported
    wa, wb = a[sheet], b[sheet]
    rows_a = [r for r in range(1, wa.max_row + 1) if wa.cell(r, 3).value is not None]
    rows_b = [r for r in range(1, wb.max_row + 1) if wb.cell(r, 3).value is not None]
    assert len(rows_a) == len(rows_b)
    for ra, rb in zip(rows_a, rows_b):
        for c in range(1, len(cols) + 1):
            ca, cb = wa.cell(ra, c), wb.cell(rb, c)
            assert _norm(ca.value) == cb.value, (sheet, ra, c)
            la = ca.hyperlink.target if ca.hyperlink else None
            lb = cb.hyperlink.target if cb.hyperlink else None
            assert la == lb, (sheet, ra, c, "link")
            if c == 1 and isinstance(wa.cell(ra, 2).value, int) and ra > 1:
                # the original had two hand-coloured Rank cells that disagreed with
                # the score key; the export always follows the key
                assert _rgb(cb) == score_fill(wa.cell(ra, 2).value)
            else:
                assert _rgb(ca) == _rgb(cb), (sheet, ra, c, "fill")
    assert wb.freeze_panes == "D2"


def test_import_report(exported):
    report, _, _ = exported
    assert report["tracker"]["findaphd"] == 55
    assert report["tracker"]["jobsac"] == 26
    assert report["tracker"]["jobsac_excluded"] == 34
    assert report["applications_linked"] == 7
    # folders that are not tracker rows stay unlinked rather than being mis-matched
    assert "Reliable and Efficient Adaptation of LLMs -- England -- Dr. Zhao (Sheffield)" in report["folders"]["unlinked"]


def test_summary_counts_match_status_file(exported):
    _, _, b = exported
    ws = b["Summary"]
    counts = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(4, 9)}
    assert counts == {
        "Applied via portal (still in process)": 5,
        "Emailed, waiting for reply": 24,
        "Not started (FindAPhD sheet)": 19,
        "jobs.ac.uk positions (not started)": 14,
        "Not shortlisted / closed": 19,
    }


def test_folder_links(db):
    from app.db.base import session_scope
    from app.db.models import Position
    from app.tracker.importer import import_all
    from sqlalchemy import select
    with session_scope() as s:
        import_all(s, XLSX, ROOT / "seed" / "folder_inventory.json", ROOT / "seed" / "applications.yaml")
    with session_scope() as s:
        by_folder = {p.folder_name: p.title for p in s.scalars(select(Position)) if p.folder_name}
    assert by_folder["Biometric Recognition Systems -- England -- Dr Sasan Mahmoodi (Southampton)"].startswith("Biometric")
    assert by_folder["Mental Health AI Person-specific Cognition -- England -- Dr Siyang Song (Exeter)"].startswith("Simulating")
    assert by_folder["Multi-Agent RL Embodied Cooperative Agents -- England -- Dr Kai-Fung Chu (Bristol)"].startswith("Multi-Agent")
