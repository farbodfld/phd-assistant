"""One-off migration of the existing tracker, position folders and applications."""
import json
import re
from datetime import date, datetime
from pathlib import Path

import openpyxl
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (Application, Contact, Document, ExcludedPosition,
                           Meta, Position)
from app.tracker.layout import (COLS_BY_SOURCE, EXCLUDED_HEADER, GREY,
                                SHEET_ABOUT, SHEET_BY_SOURCE, TICK)
from app.tracker.parsing import (cell_text, classify_document,
                                 parse_contacts, parse_deadline)


def _is_grey(cell) -> bool:
    rgb = cell.fill.fgColor.rgb if cell.fill and cell.fill.fgColor else None
    return isinstance(rgb, str) and rgb.upper().endswith(GREY)


def _guess_funding(*texts: str | None) -> str:
    t = " ".join(x for x in texts if x).lower()
    if "home fee" in t or "home rate" in t or "uk only" in t or "british/european" in t:
        return "home_only"
    if "competition funded" in t:
        return "competition"
    if "confirm international fee" in t or "confirm full international" in t or "funding not specified" in t:
        return "unclear"
    if "students worldwide" in t or "international" in t:
        return "full_international"
    return "unknown"


def import_tracker(s: Session, xlsx_path: Path) -> dict:
    wb = openpyxl.load_workbook(xlsx_path)
    counts = {}
    for source, cols in COLS_BY_SOURCE.items():
        ws = wb[SHEET_BY_SOURCE[source]]
        headers = [ws.cell(1, c).value for c in range(1, len(cols) + 1)]
        expected = [c.header for c in cols]
        if headers != expected:
            raise ValueError(f"{ws.title}: unexpected headers {headers}")
        n = 0
        r = 2
        while r <= ws.max_row:
            title = ws.cell(r, 3).value
            if title == EXCLUDED_HEADER:
                r += 1
                break
            if title is None:          # blank / stray rows (e.g. an orphan hyperlink)
                r += 1
                continue
            p = Position(source=source, sort_tiebreak=r, closed=_is_grey(ws.cell(r, 3)))
            for i, col in enumerate(cols, start=1):
                cell = ws.cell(r, i)
                if col.field == "rank":
                    continue
                if col.field == "link":
                    p.link = cell.hyperlink.target if cell.hyperlink else cell_text(cell.value)
                elif col.field in ("email_done", "portal_done"):
                    setattr(p, col.field, cell.value == TICK)
                elif col.field == "score":
                    p.score = int(cell.value) if cell.value is not None else None
                else:
                    setattr(p, col.field, cell_text(cell.value))
            p.deadline_date = parse_deadline(p.deadline_text)
            p.funding = _guess_funding(p.fit_note, p.status)
            for name, email in parse_contacts(p.supervisor_contact):
                p.contacts.append(Contact(name=name, email=email))
            s.add(p)
            n += 1
            r += 1
        # "Checked and excluded" block (jobs.ac.uk sheet)
        order = 0
        while r <= ws.max_row:
            title = ws.cell(r, 3).value
            if title:
                order += 1
                s.add(ExcludedPosition(source=source, title=str(title),
                                       reason=cell_text(ws.cell(r, 14).value), sort_order=order))
            r += 1
        counts[source] = n
        counts[f"{source}_excluded"] = order

    about = wb[SHEET_ABOUT]
    lines = [about.cell(r, 1).value for r in range(1, about.max_row + 1)]
    s.merge(Meta(key="about_sheet", value=lines))
    return counts


# ------------------------------------------------------------------ folders
_FOLDER_RE = re.compile(r"^(?P<title>.*?)\s+--\s+(?P<country>[^-]+?)\s+--\s+(?P<who>.*?)(?:\s*\((?P<uni>[^)]*)\))?\s*$")
_STOP = {"and", "for", "of", "the", "in", "with", "a", "an", "to", "ai", "phd", "england", "using", "dr", "prof"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2 and t not in _STOP}


def folder_match_score(folder: str, p: Position) -> float:
    """How well a folder name '<title> -- <country> -- <who> (<uni>)' fits a position.

    The supervisor surname must appear in the position's contact field, and the
    title words must overlap; otherwise the score is 0.
    """
    m = _FOLDER_RE.match(folder)
    if not m:
        return 0.0
    ftitle, who, uni = m.group("title"), m.group("who"), m.group("uni") or ""
    surnames = [w for w in re.findall(r"[A-Z][a-zA-Z\-]+", who)
                if w not in ("Dr", "Prof", "Professor", "Assoc", "Mr", "Ms")]
    sup = p.supervisor_contact or ""
    ft = _tokens(ftitle)
    overlap = len(ft & _tokens(p.title)) / max(1, len(ft))
    surname_hit = any(sn in sup for sn in surnames)
    if surnames and not surname_hit:
        # contact field may just say "See advert": accept only a strong title match
        # at the same university
        if overlap < 0.6 or not (uni and uni.lower() in (p.university or "").lower()):
            return 0.0
        return overlap
    if overlap < 0.25:
        return 0.0
    score = overlap + 0.6
    if uni and uni.lower() in (p.university or "").lower():
        score += 0.2
    return score


def match_folders(folders: list[str], positions: list[Position]) -> dict[str, Position | None]:
    """One folder per position: assign best pairs first (greedy on score)."""
    pairs = sorted(((folder_match_score(f, p), f, p) for f in folders for p in positions),
                   key=lambda t: -t[0])
    result: dict[str, Position | None] = {f: None for f in folders}
    used: set[int] = set()
    for score, f, p in pairs:
        if score <= 0:
            break
        if result[f] is None and id(p) not in used:
            result[f] = p
            used.add(id(p))
    return result


def import_folders(s: Session, inventory: list[list]) -> dict:
    positions = list(s.scalars(select(Position)))
    unmatched = {f for f, *_ in inventory if " -- " not in f}   # e.g. "Claude outputs", "AI Itally"
    matched = match_folders(sorted({f for f, *_ in inventory} - unmatched), positions)
    for folder, p in matched.items():
        if p is not None:
            p.folder_name = folder
    for folder, filename, size, mtime in inventory:
        if folder in unmatched:
            continue
        kind = classify_document(filename)
        if kind == "temp":
            continue
        p = matched[folder]
        s.add(Document(position_id=p.id if p else None, folder_name=folder, filename=filename,
                       kind=kind, size_bytes=size, modified_at=datetime.fromisoformat(mtime)))
    return {
        "folders": len(matched),
        "linked": sum(1 for v in matched.values() if v),
        "unlinked": sorted([k for k, v in matched.items() if not v] + list(unmatched)),
    }


def import_applications(s: Session, seed: list[dict]) -> int:
    positions = list(s.scalars(select(Position)))
    for a in seed:
        match = a.pop("match", None)
        pos = next((p for p in positions if match and match.lower() in p.title.lower()), None)
        sub = a.get("submitted_on")
        if isinstance(sub, str):
            sub = date.fromisoformat(sub)
        a["submitted_on"] = sub
        s.add(Application(position_id=pos.id if pos else None, **a))
    return len(seed)


def import_all(s: Session, xlsx: Path, inventory_json: Path, applications_yaml: Path) -> dict:
    report = {"tracker": import_tracker(s, xlsx)}
    s.flush()
    report["folders"] = import_folders(s, json.loads(Path(inventory_json).read_text()))
    report["applications"] = import_applications(s, yaml.safe_load(Path(applications_yaml).read_text()))
    s.flush()
    report["applications_linked"] = s.query(Application).filter(Application.position_id.isnot(None)).count()
    return report
