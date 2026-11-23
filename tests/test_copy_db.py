from sqlalchemy import create_engine, select

from app.db.base import session_scope
from app.db.models import Draft, Meta, Position


def test_copy_everything_and_refuse_to_overwrite(db, tmp_path):
    import pytest
    from app.ops.copy_db import copy_database
    with session_scope() as s:
        p = Position(source="findaphd", title="A")
        s.add(p)
        s.flush()
        s.add(Draft(position_id=p.id, kind="initial", to="x@y.ac.uk", subject="s", body="b", attachments=["cv.pdf"]))
        s.add(Meta(key="k", value={"a": 1}))
    target = f"sqlite:///{tmp_path / 'server.db'}"
    counts = copy_database(db, target)
    assert counts["positions"] == 1 and counts["drafts"] == 1 and counts["meta"] == 1
    with create_engine(target).connect() as c:
        from app.db.models import Draft as D
        assert c.execute(select(D.__table__.c.attachments)).scalar() == ["cv.pdf"]
    with pytest.raises(SystemExit):
        copy_database(db, target)
    assert copy_database(db, target, force=True)["positions"] == 1
