import pytest

from parsing import parse_date, parse_money


@pytest.mark.parametrize(
    "value, expected",
    [
        ("$22.40", 22.40),
        ("1,234.56", 1234.56),
        ("TOTAL 8.50 CAD", 8.50),
        (12, 12.0),
        (3.333, 3.33),
        ("", None),
        ("n/a", None),
        (None, None),
        (True, None),
    ],
)
def test_parse_money(value, expected):
    assert parse_money(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-09-25", "2026-09-25"),
        ("09/25/26", "2026-09-25"),  # two-digit year is 20xx, not 1900s/2016
        ("09/25/2026", "2026-09-25"),
        ("25/09/2026", "2026-09-25"),  # day-first when month-first is impossible
        ("Sep 25, 2026", "2026-09-25"),
        ("09/25/26 18:42", "2026-09-25"),
        ("not a date", None),
        (None, None),
    ],
)
def test_parse_date(value, expected):
    assert parse_date(value) == expected
