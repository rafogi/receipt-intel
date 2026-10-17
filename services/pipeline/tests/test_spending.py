from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import spending


def r(status="processed", category="groceries", total="10.00", gst=None, pst=None, day="2026-10-05"):
    return {"status": status, "category": category, "total": Decimal(total),
            "gst": Decimal(gst) if gst else None, "pst": Decimal(pst) if pst else None, "purchaseDate": day}


def test_summarize_counts_only_processed_receipts():
    s = spending.summarize([
        r(total="40.10", gst="1.91"),
        r(category="dining", total="25.00", gst="1.19", pst="1.67"),
        r(total="9.90"),
        r(status="needs_review", total="999.00"),
    ])
    assert s["total"] == Decimal("75.00")
    assert s["gst"] == Decimal("3.10") and s["pst"] == Decimal("1.67")
    assert s["processedCount"] == 3 and s["pendingCount"] == 1
    assert [(e["category"], e["amount"], e["count"]) for e in s["byCategory"]] == [
        ("groceries", Decimal("50.00"), 2),
        ("dining", Decimal("25.00"), 1),
    ]


def test_summarize_empty_month():
    s = spending.summarize([])
    assert s["total"] == 0 and s["byCategory"] == [] and s["pendingCount"] == 0


def test_month_bounds_handles_month_lengths():
    assert spending.month_bounds("2026-02") == ("2026-02-01", "2026-02-28")
    assert spending.month_bounds("2028-02") == ("2028-02-01", "2028-02-29")
    assert spending.month_bounds("2026-12") == ("2026-12-01", "2026-12-31")


def test_budget_progress_rounds_down_and_covers_overall():
    s = spending.summarize([r(total="84.99"), r(category="dining", total="30.00")])
    rows = spending.budget_progress(s, [
        {"category": "groceries", "monthlyLimit": Decimal("100")},
        {"category": "total", "monthlyLimit": Decimal("100")},
        {"category": "gas", "monthlyLimit": Decimal("50")},
    ])
    assert [(x["category"], x["spent"], x["percent"]) for x in rows] == [
        ("total", Decimal("114.99"), 114),
        ("groceries", Decimal("84.99"), 84),  # not 85: no early alert
        ("gas", Decimal("0"), 0),
    ]


@pytest.mark.parametrize("percent, expected", [(0, 0), (84, 0), (85, 85), (99, 85), (100, 100), (250, 100)])
def test_crossed_threshold(percent, expected):
    assert spending.crossed_threshold(percent) == expected


def test_today_local_uses_vancouver_time():
    # 03:00 UTC on Nov 1 is still Oct 31 in Vancouver.
    assert spending.today_local(datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)) == date(2026, 10, 31)


class PagedTable:
    def __init__(self):
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if "ExclusiveStartKey" not in kwargs:
            return {"Items": [{"n": 1}], "LastEvaluatedKey": {"k": 1}}
        return {"Items": [{"n": 2}]}


def test_receipts_between_follows_pages_and_includes_end_date():
    table = PagedTable()
    assert spending.receipts_between(table, "u1", "2026-10-01", "2026-10-31") == [{"n": 1}, {"n": 2}]
    cond = table.calls[0]["KeyConditionExpression"]
    assert table.calls[0]["IndexName"] == "byDate"
    # between("2026-10-01#", "2026-10-31#~"): "~" sorts after any receipt id.
    assert cond.get_expression()["values"][1].get_expression()["values"][1:] == ("2026-10-01#", "2026-10-31#~")


@pytest.mark.parametrize("utc, local", [
    (datetime(2026, 3, 8, 9, 59), date(2026, 3, 8)),    # 01:59 PST
    (datetime(2026, 7, 1, 6, 30), date(2026, 6, 30)),   # 23:30 PDT
    (datetime(2026, 7, 1, 7, 30), date(2026, 7, 1)),    # 00:30 PDT
    (datetime(2026, 11, 1, 8, 30), date(2026, 11, 1)),  # 01:30 PDT, before fall-back
    (datetime(2026, 12, 1, 7, 30), date(2026, 11, 30)), # 23:30 PST
])
def test_pacific_fallback_without_tz_database(monkeypatch, utc, local):
    monkeypatch.setattr(spending, "_ZONE", None)
    assert spending.today_local(utc.replace(tzinfo=timezone.utc)) == local
