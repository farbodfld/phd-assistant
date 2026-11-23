"""Copy every table from one database to another (local SQLite -> server PostgreSQL)."""
from sqlalchemy import create_engine, func, select, text

from app.db.base import Base


def copy_database(source_url: str, target_url: str, force: bool = False) -> dict[str, int]:
    from app.db import models  # noqa: F401  (register tables)
    src, dst = create_engine(source_url), create_engine(target_url)
    Base.metadata.create_all(dst)
    tables = Base.metadata.sorted_tables                 # parents before children
    with dst.begin() as conn:
        filled = [t.name for t in tables if conn.execute(select(func.count()).select_from(t)).scalar()]
        if filled and not force:
            raise SystemExit(f"target already has data in {', '.join(filled)}; use --force to replace it")
        for t in reversed(tables):
            conn.execute(t.delete())
    counts = {}
    with src.connect() as s_conn, dst.begin() as d_conn:
        for t in tables:
            rows = [dict(r._mapping) for r in s_conn.execute(select(t))]
            if rows:
                d_conn.execute(t.insert(), rows)
            counts[t.name] = len(rows)
        if dst.dialect.name == "postgresql":            # continue ids after the copied rows
            for t in tables:
                if "id" in t.c and t.c.id.autoincrement and counts[t.name]:
                    d_conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{t.name}', 'id'), "
                                        f"(SELECT max(id) FROM {t.name}))"))
    return counts
