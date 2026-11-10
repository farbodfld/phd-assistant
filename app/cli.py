"""Command line entry point: `phd <command>`."""
import argparse
import json
from datetime import date
from pathlib import Path

from app.config import ROOT


def main(argv=None):
    import sys
    for stream in (sys.stdout, sys.stderr):           # Persian text on the Windows console
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="phd")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="create tables")
    imp = sub.add_parser("import", help="one-off migration of the existing tracker and folders")
    imp.add_argument("xlsx", type=Path)
    imp.add_argument("--inventory", type=Path, default=ROOT / "seed" / "folder_inventory.json")
    imp.add_argument("--applications", type=Path, default=ROOT / "seed" / "applications.yaml")
    exp = sub.add_parser("export", help="write the Excel tracker")
    exp.add_argument("out", type=Path, nargs="?", default=ROOT / "exports" / "Farbod_PhD_Position_Matches.xlsx")
    sub.add_parser("seed-knowledge", help="load supervisor-reply insights from seed/insights.yaml")
    chk = sub.add_parser("check", help="run rules and refresh flags")
    chk.add_argument("--today", type=date.fromisoformat, default=None)
    sub.add_parser("gmail-auth", help="log in to Gmail once (opens the browser)")
    bf = sub.add_parser("backfill", help="import past PhD email threads (no LLM)")
    bf.add_argument("--since", type=date.fromisoformat, default=date(2026, 7, 1))
    bf.add_argument("--rebuild", action="store_true", help="forget imported email first, then import again")
    sub.add_parser("inbox-check", help="check Gmail now and process new PhD email")
    rd = sub.add_parser("reminders-due", help="list supervisors due a reminder")
    rd.add_argument("--today", type=date.fromisoformat, default=None)
    an = sub.add_parser("analyse", help="dry run: Opus analyses the last N stored incoming emails, nothing is changed")
    an.add_argument("--last", type=int, default=1)
    sub.add_parser("serve", help="run the assistant (phase 6)")
    args = ap.parse_args(argv)

    from app.db.base import init_db, session_scope
    init_db()
    if args.cmd == "init-db":
        print("tables ready")
    elif args.cmd == "import":
        from app.tracker.importer import import_all
        with session_scope() as s:
            print(json.dumps(import_all(s, args.xlsx, args.inventory, args.applications), indent=2, default=str))
    elif args.cmd == "export":
        from app.tracker.exporter import export_workbook
        with session_scope() as s:
            print(export_workbook(s, args.out, date.today()))
    elif args.cmd == "seed-knowledge":
        from app.rules.engine import seed_insights
        with session_scope() as s:
            print(f"{seed_insights(s)} new insights")
    elif args.cmd == "check":
        from app.rules.engine import run_checks
        with session_scope() as s:
            r = run_checks(s, args.today)
        print("open flags by action:", r.flags_by_action)
        for d in r.decisions:
            print("DECIDE:", d)
    elif args.cmd == "gmail-auth":
        from app.gmail.client import authorize
        print("authorised:", authorize())
    elif args.cmd == "backfill":
        from app.gmail.client import GmailClient
        from app.llm.client import LLMClient
        from app.mail.inbox import backfill, reset_email_history
        with session_scope() as s:
            if args.rebuild:
                reset_email_history(s)
                s.commit()
                print("email history cleared")
            print(backfill(s, GmailClient(), args.since, llm=LLMClient()))
    elif args.cmd == "inbox-check":
        from app.gmail.client import GmailClient
        from app.llm.client import LLMClient
        from app.mail.inbox import check_inbox
        with session_scope() as s:
            print(check_inbox(s, GmailClient(), LLMClient()))
    elif args.cmd == "reminders-due":
        from app.mail.reminders import reminders_due
        with session_scope() as s:
            for d in reminders_due(s, args.today or date.today()):
                print(f"R{d.number} | last {d.last_sent} | {'thread' if d.thread_id else 'NO THREAD'} | "
                      f"{d.position.title[:70]}")
    elif args.cmd == "analyse":
        from sqlalchemy import select
        from app.db.models import EmailMessage, Position
        from app.gmail.client import GmailClient
        from app.gmail.parse import parse_message
        from app.knowledge.loader import build_context
        from app.llm.client import LLMClient
        from app.mail.analysis import ANALYSE_INSTRUCTIONS, EmailAnalysis
        from app.mail.inbox import _prompt
        gmail, llm = GmailClient(), LLMClient()
        with session_scope() as s:
            rows = list(s.scalars(select(EmailMessage).where(
                EmailMessage.direction == "in", EmailMessage.position_id.isnot(None),
                (EmailMessage.category.is_(None)) | (EmailMessage.category != "auto_reply"))
                .order_by(EmailMessage.sent_at.desc()).limit(args.last)))
            for row in rows:
                e = parse_message(gmail.get(row.gmail_id), gmail.me)
                p = s.get(Position, row.position_id) if row.position_id else None
                a = llm.complete("analyse_email", _prompt(s, e, p), context=build_context("analyse_email"),
                                 instructions=ANALYSE_INSTRUCTIONS, output=EmailAnalysis)
                print("=" * 70)
                print(f"{e.sent_at:%Y-%m-%d} {e.sender_name}: {e.subject}")
                print("position:", p.title if p else None)
                print(a.model_dump_json(indent=2))
            s.rollback()
    elif args.cmd == "serve":
        raise SystemExit("serve is built in phase 6")


if __name__ == "__main__":
    main()
