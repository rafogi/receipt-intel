"""Spending totals and budget progress, shared by the API, budget alerts and the
weekly summary. All the math happens here, in Decimal; a model only ever
words numbers it is given (Phase 8 rule: math in tools, not in the model)."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from boto3.dynamodb.conditions import Key

from receipt_schema import CATEGORIES

OVERALL = "total"  # budget key for all categories together
BUDGET_KEYS = [OVERALL, *CATEGORIES]
ALERT_THRESHOLDS = (85, 100)  # percent of a monthly budget
CENT = Decimal("0.01")


def _pacific(now: datetime) -> datetime:
    """North American Pacific time without a tz database: DST from 02:00 on the
    second Sunday of March to 02:00 on the first Sunday of November (10:00 and
    09:00 UTC)."""
    def sunday(month: int, nth: int) -> datetime:
        first = datetime(now.year, month, 1, tzinfo=timezone.utc)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))
    dst = sunday(3, 2) + timedelta(hours=10) <= now < sunday(11, 1) + timedelta(hours=9)
    return now.astimezone(timezone(timedelta(hours=-7 if dst else -8)))


try:
    # Receipt dates are local; "this month" and "last week" follow the user's clock.
    _ZONE = ZoneInfo(os.environ.get("TIMEZONE", "America/Vancouver"))
except ZoneInfoNotFoundError:  # no tz database (e.g. Windows without tzdata)
    _ZONE = None


def today_local(now: datetime | None = None) -> date:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return (now.astimezone(_ZONE) if _ZONE else _pacific(now)).date()


def receipts_between(table, user_id: str, start: str, end: str) -> list[dict]:
    """Receipts whose purchase date is in [start, end] (ISO dates), any status.

    Uses the byDate index (dateKey = date#receiptId). "~" sorts after "#", so
    `end#~` includes every receipt on the end date.
    """
    query = {
        "IndexName": "byDate",
        "KeyConditionExpression": Key("userId").eq(user_id) & Key("dateKey").between(f"{start}#", f"{end}#~"),
    }
    items: list[dict] = []
    while True:
        resp = table.query(**query)
        items += resp.get("Items", [])
        if not resp.get("LastEvaluatedKey"):
            return items
        query["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def month_bounds(month: str) -> tuple[str, str]:
    """"2026-10" -> ("2026-10-01", "2026-10-31")."""
    year, mon = int(month[:4]), int(month[5:7])
    nxt = date(year + mon // 12, mon % 12 + 1, 1)
    return f"{month}-01", date.fromordinal(nxt.toordinal() - 1).isoformat()


def summarize(receipts: list[dict]) -> dict:
    """Totals over processed receipts only; anything still waiting on the user
    is counted separately, so a total is never quietly incomplete."""
    processed = [r for r in receipts if r.get("status") == "processed"]
    by_category: dict[str, dict] = {}
    for r in processed:
        cat = r.get("category") or "other"
        entry = by_category.setdefault(cat, {"category": cat, "amount": Decimal(0), "count": 0})
        entry["amount"] += _money(r.get("total"))
        entry["count"] += 1
    return {
        "total": sum((e["amount"] for e in by_category.values()), Decimal(0)),
        "gst": sum((_money(r.get("gst")) for r in processed), Decimal(0)),
        "pst": sum((_money(r.get("pst")) for r in processed), Decimal(0)),
        "processedCount": len(processed),
        "pendingCount": len(receipts) - len(processed),
        "byCategory": sorted(by_category.values(), key=lambda e: (-e["amount"], e["category"])),
    }


def budget_progress(summary: dict, budgets: list[dict]) -> list[dict]:
    """One row per budget: spent, limit, percent used (rounded down, so 84.9% never reads as 85%)."""
    spent = {e["category"]: e["amount"] for e in summary["byCategory"]}
    rows = []
    for b in sorted(budgets, key=lambda b: BUDGET_KEYS.index(b["category"]) if b["category"] in BUDGET_KEYS else 99):
        limit = _money(b.get("monthlyLimit"))
        used = summary["total"] if b["category"] == OVERALL else spent.get(b["category"], Decimal(0))
        rows.append({
            "category": b["category"],
            "limit": limit,
            "spent": used,
            "percent": int(used * 100 / limit) if limit > 0 else 0,
        })
    return rows


def crossed_threshold(percent: int) -> int:
    """Highest alert threshold reached, or 0."""
    return max((t for t in ALERT_THRESHOLDS if percent >= t), default=0)


def _money(value) -> Decimal:
    if value is None:
        return Decimal(0)
    return Decimal(str(value)).quantize(CENT)
