"""Rebuild the Excel tracker from the database, in exactly the current format."""
from collections import OrderedDict
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ExcludedPosition, Meta, Position
from app.tracker.layout import (APPLY_FILLS, COLS_BY_SOURCE, EXCLUDED_HEADER,
                                GREY, HEADER_FILL, LINK_BLUE, LINK_TEXT,
                                SHEET_ABOUT, SHEET_BY_SOURCE, SHEET_SUMMARY,
                                TICK, score_fill)

THIN = Side(style="thin")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
WRAP = Alignment(wrap_text=True, vertical="center")


def _fill(rgb: str) -> PatternFill:
    return PatternFill("solid", fgColor=rgb)


def _header(ws, row: int, values: list[str]):
    for c, v in enumerate(values, start=1):
        cell = ws.cell(row, c, v)
        cell.fill = _fill(HEADER_FILL)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = CENTER
        cell.border = BORDER


def sorted_positions(s: Session, source: str) -> list[Position]:
    rows = list(s.scalars(select(Position).where(Position.source == source)))
    return sorted(rows, key=lambda p: (-(p.score or 0), p.sort_tiebreak, p.id))


def _write_positions(ws, positions: list[Position], cols):
    _header(ws, 1, [c.header for c in cols])
    ws.row_dimensions[1].height = 32
    for i, c in enumerate(cols, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = c.width
    for r, p in enumerate(positions, start=2):
        for i, col in enumerate(cols, start=1):
            if col.field == "rank":
                value = r - 1
            elif col.field in ("email_done", "portal_done"):
                value = TICK if getattr(p, col.field) else None
            elif col.field == "link":
                value = LINK_TEXT if p.link else None
            else:
                value = getattr(p, col.field)
            cell = ws.cell(r, i, value)
            cell.alignment = CENTER
            cell.border = BORDER
            if col.bold:
                cell.font = Font(bold=True)
            if col.field == "link" and p.link:
                cell.hyperlink = p.link
                cell.font = Font(color=LINK_BLUE, underline="single")
        # colours: score on A:B, apply method on G, grey C:last when closed
        sf = score_fill(p.score)
        if sf:
            ws.cell(r, 1).fill = ws.cell(r, 2).fill = _fill(sf)
        if p.apply_method in APPLY_FILLS:
            ws.cell(r, 7).fill = _fill(APPLY_FILLS[p.apply_method])
        if p.closed:
            for c in range(3, len(cols) + 1):
                ws.cell(r, c).fill = _fill(GREY)
    last = ws.cell(1, len(cols)).column_letter
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = f"A1:{last}{len(positions) + 1}"
    return len(positions) + 1


def _short_uni(u: str | None) -> str:
    u = (u or "").split("(")[0].strip()
    for prefix in ("The University of ", "University of "):
        if u.startswith(prefix):
            return u[len(prefix):]
    return u


def summary_counts(s: Session) -> "OrderedDict[str, int]":
    ps = list(s.scalars(select(Position)))
    open_ = [p for p in ps if not p.closed]
    return OrderedDict([
        ("Applied via portal (still in process)", sum(p.portal_done for p in open_)),
        ("Emailed, waiting for reply", sum(p.email_done and not p.portal_done for p in open_)),
        ("Not started (FindAPhD sheet)", sum(p.source == "findaphd" and not p.email_done and not p.portal_done for p in open_)),
        ("jobs.ac.uk positions (not started)", sum(p.source == "jobsac" and not p.email_done and not p.portal_done for p in open_)),
        ("Not shortlisted / closed", sum(p.closed for p in ps)),
    ])


def next_deadlines(s: Session, today: date, limit: int = 7):
    ps = [p for p in s.scalars(select(Position))
          if not p.closed and not p.portal_done and p.deadline_date and p.deadline_date >= today]
    groups: "OrderedDict[date, list[Position]]" = OrderedDict()
    for p in sorted(ps, key=lambda p: (p.deadline_date, -(p.score or 0))):
        groups.setdefault(p.deadline_date, []).append(p)
    out = []
    for d, items in list(groups.items())[:limit]:
        text = "; ".join(f"{_short_uni(p.university)}: {p.title}" for p in items)
        routes = sorted({("Email, then portal" if p.apply_method == "Email + Portal" else (p.apply_method or "?")) for p in items})
        out.append((f"{d.day} {d.strftime('%b %Y')}", text, ", ".join(routes)))
    return out


def _write_summary(ws, s: Session, today: date):
    for col, w in zip("ABCD", (34, 70, 45, 10)):
        ws.column_dimensions[col].width = w
    ws.cell(1, 1, f"PhD search status (updated {today.isoformat()})").font = Font(bold=True)
    _header(ws, 3, ["Stage", "Count"])
    r = 4
    for label, n in summary_counts(s).items():
        for c, v in enumerate((label, n), start=1):
            cell = ws.cell(r, c, v)
            cell.border, cell.alignment = BORDER, WRAP
        r += 1
    r += 1
    _header(ws, r, ["Next deadlines", "Position", "Route"])
    r += 1
    for row in next_deadlines(s, today):
        for c, v in enumerate(row, start=1):
            cell = ws.cell(r, c, v)
            cell.border, cell.alignment = BORDER, WRAP
        r += 1
    r += 1
    _header(ws, r, ["Colour key (Score)", "Columns"])
    key = [
        ("Green: strong fit (70+)", "63BE7B", "Email ✓ = supervisor emailed; Portal ✓ = formal application submitted; both ticked = both done"),
        ("Yellow: good fit (50-69)", "FFEB84", "Reminder 1/2 = follow-up dates"),
        ("Orange: moderate fit (35-49)", "F8B26A", "Grey row = closed, rejected or not suitable (reason at the start of Status)"),
        ("Red: weak fit (<35)", "F8696B", "Apply Method: Email = contact supervisor only; Portal = university application form; "
                                          "Email + Portal = email supervisor first, then formal portal application"),
    ]
    for label, rgb, note in key:
        r += 1
        a = ws.cell(r, 1, label)
        a.fill, a.border, a.alignment = _fill(rgb), BORDER, WRAP
        b = ws.cell(r, 2, note)
        b.border, b.alignment = BORDER, WRAP


def export_workbook(s: Session, out_path: Path, today: date | None = None) -> Path:
    today = today or date.today()
    wb = Workbook()
    _write_summary(wb.active, s, today)
    wb.active.title = SHEET_SUMMARY

    for source, cols in COLS_BY_SOURCE.items():
        ws = wb.create_sheet(SHEET_BY_SOURCE[source])
        last_row = _write_positions(ws, sorted_positions(s, source), cols)
        excluded = list(s.scalars(select(ExcludedPosition).where(ExcludedPosition.source == source)
                                  .order_by(ExcludedPosition.sort_order)))
        if excluded:
            r = last_row + 2
            ws.cell(r, 3, EXCLUDED_HEADER).font = Font(bold=True)
            for e in excluded:
                r += 1
                for c, v in ((3, e.title), (14, e.reason)):
                    cell = ws.cell(r, c, v)
                    cell.alignment, cell.border = CENTER, BORDER

    about = wb.create_sheet(SHEET_ABOUT)
    about.column_dimensions["A"].width = 40
    about.column_dimensions["B"].width = 90
    meta = s.get(Meta, "about_sheet")
    for r, line in enumerate((meta.value if meta else []) or [], start=1):
        if line is not None:
            about.cell(r, 1, line).alignment = Alignment(wrap_text=False)

    # sheet order as in the original file
    wb._sheets = [wb[SHEET_SUMMARY], wb[SHEET_BY_SOURCE["findaphd"]], wb[SHEET_BY_SOURCE["jobsac"]], about]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
