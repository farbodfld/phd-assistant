from app.rules.text_checks import (check_outgoing_text, find_dash_connectors,
                                   find_fabrications, find_tone_problems,
                                   fix_dash_connectors)


def test_dash_connectors_found():
    assert find_dash_connectors("I am excited — truly excited.")
    assert find_dash_connectors("My work - mostly on agents - fits well.")
    assert find_dash_connectors("Agents – and tools")


def test_allowed_dashes():
    assert not find_dash_connectors("I studied from 2019-2024 and 2019–2024.")
    assert not find_dash_connectors("state-of-the-art, semi-supervised, Hyperspectral-imaging.")


def test_dash_fix():
    assert fix_dash_connectors("I am ready — CV attached.") == "I am ready, CV attached."
    assert fix_dash_connectors("Work - mostly agents") == "Work, mostly agents"


def test_fabrications():
    kinds = lambda t: [i.message for i in find_fabrications(t)]
    assert kinds("I hold a BSc in Computer Science from SBU.")
    assert kinds("I graduated from my MSc with Distinction.")
    assert kinds("I published a paper on anomaly detection.")
    assert kinds("I did well in Discrete Mathematics.")
    assert kinds("I work at Slim Chickens.")
    assert not kinds("I hold a BSc in Computer Engineering and an MSc in Data Science with Merit. "
                     "My dissertation scored 75 and Research Skills earned a Distinction.")


def test_tone():
    assert find_tone_problems("Should I apply formally?")
    assert find_tone_problems("Do you recommend I apply?")
    assert not find_tone_problems("I am ready to submit a formal application.")


def test_combined():
    assert check_outgoing_text("Fine text, with commas.", outreach=True) == []
