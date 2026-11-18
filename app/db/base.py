from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None


def get_engine(url: str | None = None):
    global _engine, _Session
    if url is not None or _engine is None:
        url = url or get_settings().database_url
        if url.startswith("sqlite:///"):
            from pathlib import Path
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, future=True)
        if url.startswith("sqlite"):
            _sqlite_pragmas(_engine)
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def _sqlite_pragmas(engine):
    """WAL lets a reader and a writer work at the same time (the LLM cost log is written from its own
    session while a workflow's session is open), and busy_timeout waits instead of failing at once."""
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


def init_db(url: str | None = None):
    from app.db import models  # noqa: F401  (register tables)
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope():
    get_engine()
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
