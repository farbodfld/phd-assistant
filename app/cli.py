"""Command line entry point: `phd <command>`."""
import argparse
import json
from datetime import date
from pathlib import Path

from app.config import ROOT


def main(argv=None):
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
    elif args.cmd == "serve":
        raise SystemExit("serve is built in phase 6")


if __name__ == "__main__":
    main()
