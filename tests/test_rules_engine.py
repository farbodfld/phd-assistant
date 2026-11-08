from datetime import date

from sqlalchemy import select

from app.db.models import Application, Flag, Insight, Position

TODAY = date(2026, 11, 8)


def _pos(s, title, uni, score=60, deadline=date(2026, 12, 1), **kw):
    p = Position(source="findaphd", title=title, university=uni, score=score,
                 deadline_date=deadline, deadline_text=str(deadline), **kw)
    s.add(p)
    return p


def _flags(s, kind=None):
    q = select(Flag).where(Flag.status == "open")
    if kind:
        q = q.where(Flag.kind == kind)
    return list(s.scalars(q))


def test_oxford_limit_asks_for_a_decision(db):
    from app.db.base import session_scope
    from app.rules.engine import run_checks
    with session_scope() as s:
        s.add(Application(university="University of Oxford", project="CEMS", outcome="pending"))
        for i in range(3):
            _pos(s, f"Oxford project {i}", "University of Oxford (Botnar)", score=80 - i)
        _pos(s, "Applied already", "University of Oxford", portal_done=True)
        s.flush()
        report = run_checks(s, TODAY)
        lim = _flags(s, "application_limit")
        assert len(lim) == 3 and all(f.action == "decide" for f in lim)
        assert "Used 1 of 3" in lim[0].message
        assert any("Oxford" in d for d in report.decisions)
        assert _flags(s, "ai_policy")            # Oxford bans AI-written statements


def test_no_limit_flag_when_places_suffice(db):
    from app.db.base import session_scope
    from app.rules.engine import run_checks
    with session_scope() as s:
        _pos(s, "Only one", "University of Oxford")
        s.flush()
        run_checks(s, TODAY)
        assert not _flags(s, "application_limit")


def test_exeter_funding_question_is_a_followup(db):
    from app.db.base import session_scope
    from app.rules.engine import run_checks
    with session_scope() as s:
        _pos(s, "R2T2-Med", "University of Exeter", funding="unclear")
        s.flush()
        run_checks(s, TODAY)
        f = _flags(s, "funding_unclear")
        assert len(f) == 1 and f[0].action == "followup"


def test_university_insight_reaches_new_positions(db):
    from app.db.base import session_scope
    from app.rules.engine import run_checks
    with session_scope() as s:
        s.add(Insight(scope_type="university", scope_value="Southampton", source="email",
                      text="University funding is only for British/European students."))
        _pos(s, "A brand new Southampton project", "University of Southampton")
        s.flush()
        run_checks(s, TODAY)
        assert "British/European" in _flags(s, "insight")[0].message


def test_flags_resolve_when_condition_disappears(db):
    from app.db.base import session_scope
    from app.rules.engine import run_checks
    with session_scope() as s:
        p = _pos(s, "Late one", "Some University", deadline=date(2026, 11, 1))
        s.flush()
        run_checks(s, TODAY)
        assert _flags(s, "deadline_passed")
        p.closed = True
        run_checks(s, TODAY)
        assert not _flags(s, "deadline_passed")


def test_outside_england_is_blocked(db):
    from app.db.base import session_scope
    from app.rules.engine import run_checks
    with session_scope() as s:
        _pos(s, "Edinburgh ML", "University of Edinburgh")
        s.flush()
        run_checks(s, TODAY)
        assert _flags(s, "location")[0].action == "block"
