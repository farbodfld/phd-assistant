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
    dv = sub.add_parser("discover", help="find new positions and update the tracker")
    dv.add_argument("--source", choices=["all", "jobsac", "findaphd"], default="all",
                    help="which site to sweep (default: both, jobs.ac.uk first)")
    dv.add_argument("--max-pages", type=int, help="optional limit for a partial run (default: all pages)")
    dv.add_argument("--today", type=date.fromisoformat, default=None)
    dv.add_argument("--retry-skipped", action="store_true",
                    help="screen again the adverts earlier judged off topic (after a rule change)")
    rc = sub.add_parser("discover-recheck", help="re-screen rows added by discovery with the current rules")
    rc.add_argument("--apply", action="store_true", help="make the changes (default: only list them)")
    rc.add_argument("--rescore", action="store_true",
                    help="reopen each jobs.ac.uk advert and screen it again with the current scoring (small model cost), "
                         "and let the next discover run re-screen adverts skipped as off topic or low fit")
    sub.add_parser("probe-sites", help="check that jobs.ac.uk can be read from here; saves samples")
    sub.add_parser("probe-findaphd", help="test live access to a FindAPhD list and advert from this host")
    ot = sub.add_parser("outreach", help="draft due reminders and first emails now (Gmail drafts until phase 6)")
    ot.add_argument("--limit", type=int, help="at most this many first emails in this run")
    ot.add_argument("--today", type=date.fromisoformat, default=None)
    di = sub.add_parser("draft-initial", help="draft the first email for one position now")
    di.add_argument("position_id", type=int)
    for name, kind in (("write-statement", "statement"), ("write-proposal", "proposal")):
        w = sub.add_parser(name, help=f"write a {kind} (Word and PDF) into the position folder")
        w.add_argument("position_id", type=int)
    dl = sub.add_parser("drafts", help="list drafts waiting for you")
    dl.add_argument("--all", action="store_true", help="include sent and deleted drafts")
    ds = sub.add_parser("draft-show", help="print one draft in full")
    ds.add_argument("draft_id", type=int)
    sub.add_parser("sync-drafts", help="push pending drafts to Gmail and record the ones you sent")
    idr = sub.add_parser("import-drafts", help="add first-email drafts written in a session with Claude (no model cost)")
    idr.add_argument("file", type=Path)
    sd = sub.add_parser("send-drafts", help="send drafts you have reviewed (their Gmail drafts, with your edits)")
    sd.add_argument("ids", type=int, nargs="*", help="draft numbers from `phd drafts`")
    sd.add_argument("--all", action="store_true", help="every pending draft")
    sd.add_argument("--except", dest="skip", type=int, nargs="*", default=[], help="draft numbers to leave out")
    cp = sub.add_parser("copy-db", help="copy all data from another database into DATABASE_URL (for deployment)")
    cp.add_argument("source", help="e.g. sqlite:////data/phd.db")
    cp.add_argument("--force", action="store_true", help="replace data already in the target")
    sub.add_parser("bot", help="run only the Telegram bot (Approve / Edit / Deny drafts)")
    sub.add_parser("telegram-test", help="send a test message to your Telegram")
    sub.add_parser("serve", help="run daily discovery on jobs.ac.uk and FindAPhD")
    args = ap.parse_args(argv)

    if args.cmd == "copy-db":
        from app.config import get_settings
        from app.ops.copy_db import copy_database
        for table, n in copy_database(args.source, get_settings().database_url, args.force).items():
            print(f"{table:20} {n}")
        return

    if args.cmd == "probe-findaphd":
        from app.discovery.findaphd_browser import FindAPhDBrowser
        from app.discovery.findaphd import FetchFailed, probe
        fetcher = FindAPhDBrowser()
        try:
            print("FindAPhD listing and detail accessible:", probe(fetcher))
        except (FetchFailed, ValueError) as exc:
            raise SystemExit(f"FindAPhD access probe failed: {exc}") from None
        finally:
            fetcher.close()
        return

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
    elif args.cmd == "discover":
        if args.max_pages is not None and args.max_pages < 1:
            ap.error("--max-pages must be positive")
        from app.discovery.worker import discover
        if args.retry_skipped:
            from app.discovery.pipeline import forget_skipped
            with session_scope() as s:
                print(f"{forget_skipped(s)} skipped advert(s) queued for screening again")
        failed = False
        for source in (["jobsac", "findaphd"] if args.source == "all" else [args.source]):
            report = discover(args.today, args.max_pages, source=source)
            if report is None:
                raise SystemExit("Discovery is already running")
            print(report.summary())
            failed = failed or bool(report.errors)
        if failed:
            raise SystemExit(1)
    elif args.cmd == "discover-recheck":
        from app.discovery.pipeline import forget_skipped, recheck_discovered
        fetcher = llm = None
        if args.rescore:
            from app.discovery.fetch import HttpFetcher
            from app.llm.client import LLMClient
            fetcher, llm = HttpFetcher(delay=3.5), LLMClient()
        try:
            with session_scope() as s:
                out = recheck_discovered(s, apply=args.apply, fetcher=fetcher, llm=llm)
                for p, verdict, reason in out:
                    print(f"{p.score or 0:>3}  {verdict:9}  {p.title[:70]}  |  {reason[:80]}")
                moved = sum(v != "rescored" for _, v, _ in out)
                print(f"{moved} row(s) {'moved out of the tracker' if args.apply else 'would leave the tracker'}, "
                      f"{len(out) - moved} rescored{'' if args.apply else ' (dry run; add --apply)'}")
                if args.rescore and args.apply:
                    print(f"{forget_skipped(s)} skipped advert(s) will be screened again by the next discover run")
                    s.commit()
        finally:
            if fetcher:
                fetcher.close()
    elif args.cmd == "probe-sites":
        from app.discovery.probe import probe
        out = ROOT / "data" / "samples"
        probe(out)
        print(f"samples saved in {out}")
    elif args.cmd == "outreach":
        from app.discovery.worker import outreach
        report = outreach(args.today, args.limit)
        print(report.summary())
    elif args.cmd in ("draft-initial", "write-statement", "write-proposal"):
        from app.db.models import Position
        from app.discovery.fetch import HttpFetcher
        from app.llm.client import LLMClient
        from app.outreach.advert import advert_text
        fetcher = HttpFetcher()
        try:
            with session_scope() as s:
                p = s.get(Position, args.position_id)
                if p is None:
                    raise SystemExit(f"no position #{args.position_id}")
                if args.cmd == "draft-initial":
                    from app.db.models import Meta
                    from app.outreach.initial import draft_initial
                    m = s.get(Meta, f"outreach:{p.id}")
                    if m:
                        s.delete(m)
                    d = draft_initial(s, p, LLMClient(), date.today(), fetcher)
                    print(f"draft #{d.id} to {d.to}: {d.subject}" if d else "no draft (see notifications)")
                else:
                    from app.outreach.documents import write_document
                    kind = "statement" if args.cmd == "write-statement" else "proposal"
                    w = write_document(s, p, kind, LLMClient(), advert_text(p, fetcher))
                    print(w.pdf)
                    print(w.docx)
                    for line in w.notes_fa + w.issues:
                        print("-", line)
        finally:
            fetcher.close()
    elif args.cmd == "drafts":
        from sqlalchemy import select
        from app.db.models import Draft
        with session_scope() as s:
            q = select(Draft).order_by(Draft.id)
            if not args.all:
                q = q.where(Draft.status.in_(("pending", "approved")))
            for d in s.scalars(q):
                flag = " ⚠" if d.issues else ""
                print(f"#{d.id:<4} {d.status:8} {d.kind:8} {d.to[:38]:38} {d.subject[:60]}{flag}")
    elif args.cmd == "draft-show":
        from app.db.models import Draft
        with session_scope() as s:
            d = s.get(Draft, args.draft_id)
            if d is None:
                raise SystemExit(f"no draft #{args.draft_id}")
            print(f"To: {d.to}\nSubject: {d.subject}\nStatus: {d.status}\n")
            print(d.body)
            for a in d.attachments or []:
                print("Attachment:", a)
            for n in (d.notes or []) + (d.issues or []):
                print("-", n)
    elif args.cmd == "send-drafts":
        from sqlalchemy import select
        from app.db.models import Draft
        from app.gmail.client import GmailClient
        from app.outreach.gmail_sync import send_approved
        if not args.ids and not args.all:
            ap.error("give draft numbers, or --all")
        gmail = GmailClient()
        with session_scope() as s:
            ids = args.ids or list(s.scalars(select(Draft.id).where(Draft.status == "pending")))
            ids = [i for i in ids if i not in set(args.skip)]
            r = send_approved(s, gmail, ids, date.today())
            print(f"sent: {r['sent']}")
            if r["missing"]:
                print(f"not sent (no Gmail draft, you may have deleted it): {r['missing']}")
            print(f"recorded on the tracker: {len(r['recorded'])}")
    elif args.cmd == "import-drafts":
        from app.outreach.importer import import_drafts, load
        with session_scope() as s:
            r = import_drafts(s, load(args.file), date.today())
            print(f"created drafts: {r.created}")
            for line in r.skipped:
                print("skipped:", line)
            try:
                from app.gmail.client import GmailClient
                from app.outreach.gmail_sync import push_drafts
                print(f"Gmail drafts created: {push_drafts(s, GmailClient())}")
            except Exception as exc:
                print(f"Gmail drafts not created now ({type(exc).__name__}); the next run will do it")
    elif args.cmd == "sync-drafts":
        from app.gmail.client import GmailClient
        from app.outreach.gmail_sync import push_drafts, sync_drafts
        gmail = GmailClient()
        with session_scope() as s:
            done = sync_drafts(s, gmail, date.today())
            print(f"sent: {len(done['sent'])}, deleted: {len(done['deleted'])}, "
                  f"new Gmail drafts: {push_drafts(s, gmail)}")
    elif args.cmd in ("bot", "telegram-test"):
        import logging
        from app.telegram.bot import make_bot
        bot = make_bot()
        if bot is None:
            raise SystemExit("set TELEGRAM_BOT_TOKEN in .env first (from @BotFather)")
        if args.cmd == "telegram-test":
            if not bot.owner:
                raise SystemExit("TELEGRAM_OWNER_ID is not set: run `phd bot`, send /start to your bot, "
                                 "and put the number it replies with in .env")
            bot.say("✅ PhD Assistant: پیام آزمایشی. ربات کار می‌کنه.")
            print("sent")
        else:
            logging.basicConfig(level=logging.INFO)
            logging.getLogger("httpx").setLevel(logging.WARNING)      # its request log prints the bot token
            print("Telegram bot running. Ctrl+C to stop.")
            bot.run_forever()
    elif args.cmd == "serve":
        from app.discovery.worker import serve
        serve()


if __name__ == "__main__":
    main()
