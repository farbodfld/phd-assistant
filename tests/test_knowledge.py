from app.knowledge.loader import build_context, institutions_for, load_profile


def test_profile_has_the_verified_degree_titles():
    p = load_profile()
    degrees = [e["degree"] for e in p["education"]]
    assert "BSc Computer Engineering" in degrees
    assert p["education"][0]["classification"] == "Merit"
    assert p["publications"] == []


def test_context_for_emails_has_rules_and_profile():
    ctx = build_context("initial_email")
    assert ctx[0].startswith("## SOP 00_mission_and_hard_rules")
    assert any("10_outreach_emails" in c for c in ctx)
    assert "verified profile" in ctx[-1]
    assert build_context("initial_email") == ctx        # stable order, so caching works


def test_classification_context_has_no_profile():
    assert not any("verified profile" in c for c in build_context("classify_email"))


def test_institution_match():
    assert institutions_for("University of Oxford (Botnar Research Centre)")[0]["application_limit"]["max"] == 3
    assert institutions_for("Nowhere University") == []
