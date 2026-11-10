from datetime import datetime

from app.gmail.parse import parse_message, strip_quoted
from tests.fakes import make_msg


def test_parse_plain_and_direction():
    raw = make_msg("m1", "t1", "Dr Jane Doe <J.Doe@Uni.ac.uk>", "farbodfooladi@gmail.com",
                   "Re: PhD enquiry", "Thanks Farbod.\n\nOn Mon, 13 Oct 2026 Farbod wrote:\n> old text",
                   datetime(2026, 10, 14, 9, 0))
    e = parse_message(raw, "farbodfooladi@gmail.com")
    assert e.sender == "j.doe@uni.ac.uk" and e.sender_name == "Dr Jane Doe"
    assert e.direction == "in" and e.body == "Thanks Farbod."
    assert e.sent_at == datetime(2026, 10, 14, 9, 0)


def test_parse_html_and_outgoing():
    raw = make_msg("m2", "t1", "farbodfooladi@gmail.com", "j.doe@uni.ac.uk", "PhD enquiry",
                   "<p>Dear Dr Doe,</p><p>I am writing&nbsp;about</p>", datetime(2026, 10, 13), html=True)
    e = parse_message(raw, "farbodfooladi@gmail.com")
    assert e.direction == "out" and e.to == ["j.doe@uni.ac.uk"]
    assert "Dear Dr Doe," in e.body and "<p>" not in e.body


def test_strip_quoted_keeps_the_reply():
    assert strip_quoted("Yes, please apply.\n\n-----Original Message-----\nFrom: x") == "Yes, please apply."
