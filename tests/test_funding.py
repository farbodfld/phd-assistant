import pytest

from app.rules.funding import classify_funding


@pytest.mark.parametrize("text,expected", [
    ("UCL UELA: 4 yrs home fees + UCL award covering the international fee difference", "full_international"),
    ("Home-rate fees only", "home_only"),
    ("UK applicants only", "home_only"),
    ("China Scholarship Council, Chinese nationals only", "home_only"),
    ("International applicants pay fee difference", "partial"),
    ("Competition funded (Students Worldwide)", "competition"),
    ("funding of fees and a stipend of £21,805", "unclear"),
    ("fully funded incl. international (waiver)", "unclear"),
    ("Funded PhD (Students Worldwide)", "full_international"),
    ("", "unknown"),
])
def test_classify(text, expected):
    assert classify_funding(text)[0] == expected
