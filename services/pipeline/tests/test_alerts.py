from decimal import Decimal

import pytest
from botocore.exceptions import ClientError

import alerts

USER = "1a2b3c4d-0000-4000-8000-000000000001"


def stream_record(status="processed", day="2026-10-05", user=USER):
    return {"eventName": "MODIFY", "dynamodb": {"NewImage": {
        "userId": {"S": user}, "purchaseDate": {"S": day}, "status": {"S": status}}}}


class Receipts:
    def __init__(self, items):
        self.items = items

    def query(self, **kwargs):
        return {"Items": self.items}


class Budgets:
    """Enough of a DynamoDB table to honour the alert-mark conditions."""

    def __init__(self, budgets):
        self.items = {b["category"]: dict(b) for b in budgets}
        self.fail_marks = False

    def query(self, **kwargs):
        return {"Items": [dict(v) for v in self.items.values()]}

    def update_item(self, Key, UpdateExpression, **kw):
        item = self.items.get(Key["category"])
        if item is None:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if "if_not_exists" in UpdateExpression:
            item.setdefault("alerts", {})
            return
        month = kw["ExpressionAttributeNames"]["#m"]
        if UpdateExpression.startswith("REMOVE"):
            item["alerts"].pop(month, None)
            return
        value = kw["ExpressionAttributeValues"].get(":t", kw["ExpressionAttributeValues"].get(":p"))
        if "ConditionExpression" in kw and item["alerts"].get(month, 0) >= value:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        item["alerts"][month] = value


class Sns:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def publish(self, **kwargs):
        if self.fail:
            raise RuntimeError("sns down")
        self.sent.append(kwargs)


def receipt(total, category="groceries", status="processed"):
    return {"status": status, "category": category, "total": Decimal(total), "purchaseDate": "2026-10-05"}


@pytest.fixture
def setup(monkeypatch):
    def make(receipts, budgets, sns=None):
        res = {"receipts": Receipts(receipts), "budgets": Budgets(budgets), "sns": sns or Sns()}
        monkeypatch.setattr(alerts, "tables", lambda: res)
        return res
    return make


def test_touched_keeps_processed_receipts_only():
    records = [stream_record(), stream_record(day="2026-10-20"), stream_record(status="needs_review", day="2026-09-01"),
               {"eventName": "REMOVE", "dynamodb": {"OldImage": {}}}]
    assert alerts.touched(records) == {(USER, "2026-10")}


def test_alert_at_85_then_100_each_once(setup):
    res = setup([receipt("86.00")], [{"category": "groceries", "monthlyLimit": Decimal("100")}])
    assert alerts.handler({"Records": [stream_record()]}, None) == {"alertsSent": 1}
    assert "86%" in res["sns"].sent[0]["Subject"]

    # Same spending again (a retry or an unrelated edit): no repeat.
    assert alerts.handler({"Records": [stream_record()]}, None) == {"alertsSent": 0}

    res["receipts"].items.append(receipt("20.00"))
    assert alerts.handler({"Records": [stream_record()]}, None) == {"alertsSent": 1}
    assert "over 106%" in res["sns"].sent[1]["Subject"]
    assert res["budgets"].items["groceries"]["alerts"] == {"2026-10": 100}


def test_jumping_straight_past_100_sends_one_alert(setup):
    res = setup([receipt("150.00")], [{"category": "groceries", "monthlyLimit": Decimal("100")}])
    alerts.handler({"Records": [stream_record()]}, None)
    assert len(res["sns"].sent) == 1 and "reached" in res["sns"].sent[0]["Message"]


def test_overall_budget_and_unbudgeted_categories(setup):
    res = setup([receipt("50.00"), receipt("40.00", category="dining")],
                [{"category": "total", "monthlyLimit": Decimal("100")}])
    alerts.handler({"Records": [stream_record()]}, None)
    assert [m["Subject"] for m in res["sns"].sent] == ["Overall budget at 90% for 2026-10"]


def test_no_budgets_means_no_receipt_query(setup):
    res = setup([], [])
    res["receipts"].query = None  # would fail if called
    assert alerts.handler({"Records": [stream_record()]}, None) == {"alertsSent": 0}


def test_failed_publish_unmarks_so_the_retry_sends_it(setup):
    res = setup([receipt("90.00")], [{"category": "groceries", "monthlyLimit": Decimal("100")}], sns=Sns(fail=True))
    with pytest.raises(RuntimeError):
        alerts.handler({"Records": [stream_record()]}, None)
    assert res["budgets"].items["groceries"]["alerts"] == {}

    res["sns"].fail = False
    assert alerts.handler({"Records": [stream_record()]}, None) == {"alertsSent": 1}
