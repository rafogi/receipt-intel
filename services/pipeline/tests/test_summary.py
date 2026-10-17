from datetime import date
from decimal import Decimal

import pytest

import summary

USER = "1a2b3c4d-0000-4000-8000-000000000001"


class Table:
    def __init__(self, items, waiting=0):
        self.items, self.waiting = items, waiting

    def query(self, **kwargs):
        if kwargs.get("Select") == "COUNT":
            return {"Count": self.waiting}
        expr = kwargs["KeyConditionExpression"].get_expression()["values"][1].get_expression()["values"]
        start, end = expr[1][:10], expr[2][:10]
        return {"Items": [i for i in self.items if start <= i["purchaseDate"] <= end]}


class Budgets:
    def __init__(self, items):
        self.items = items

    def query(self, **kwargs):
        return {"Items": self.items}


class Cognito:
    def get_paginator(self, name):
        class P:
            def paginate(self, **kw):
                return [{"Users": [
                    {"Enabled": True, "Attributes": [{"Name": "sub", "Value": USER}, {"Name": "email", "Value": "x"}]},
                    {"Enabled": False, "Attributes": [{"Name": "sub", "Value": "disabled"}]},
                ]}]
        return P()


class Bedrock:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("throttled")
        return {"output": {"message": {"content": [{"text": "You spent $62.50 last week, mostly groceries."}]}}}


class Sns:
    def __init__(self):
        self.sent = []

    def publish(self, **kwargs):
        self.sent.append(kwargs)


def receipt(day, total, category="groceries", status="processed"):
    return {"purchaseDate": day, "total": Decimal(total), "category": category, "status": status}


@pytest.fixture
def setup(monkeypatch):
    def make(items, budgets=(), bedrock=None, waiting=0):
        c = {"receipts": Table(items, waiting), "budgets": Budgets(list(budgets)), "cognito": Cognito(),
             "bedrock": bedrock or Bedrock(), "sns": Sns()}
        monkeypatch.setattr(summary, "clients", lambda: c)
        monkeypatch.setattr(summary, "today_local", lambda: date(2026, 10, 19))  # a Monday
        return c
    return make


def test_last_week_is_the_previous_monday_to_sunday():
    assert summary.last_week(date(2026, 10, 19)) == ("2026-10-12", "2026-10-18")  # Monday
    assert summary.last_week(date(2026, 10, 25)) == ("2026-10-12", "2026-10-18")  # Sunday


def test_summary_figures_come_from_code(setup):
    c = setup(
        [receipt("2026-10-13", "40.00"), receipt("2026-10-17", "22.50", "dining"),
         receipt("2026-10-02", "100.00"), receipt("2026-10-11", "5.00"),  # earlier this month, not last week
         receipt("2026-10-14", "999.00", status="needs_review")],
        budgets=[{"category": "groceries", "monthlyLimit": Decimal("160")}],
        waiting=1,
    )
    assert summary.handler({}, None) == {"summariesSent": 1}
    sent = c["sns"].sent[0]
    assert sent["Subject"] == "Your week: $62.50 spent"
    assert "mostly groceries" in sent["Message"]
    assert "groceries budget: $145.00 of $160.00 (90%)" in sent["Message"]
    assert "1 receipts are waiting" in sent["Message"]
    facts = c["bedrock"].calls[0]["messages"][0]["content"][0]["text"]
    assert '"total": "62.50"' in facts and "999" not in facts  # unreviewed receipts never counted


def test_model_failure_still_sends_plain_figures(setup):
    c = setup([receipt("2026-10-13", "40.00")], bedrock=Bedrock(fail=True))
    summary.handler({}, None)
    assert c["sns"].sent[0]["Message"].startswith("Last week (2026-10-12 to 2026-10-18): $40.00")


def test_quiet_user_gets_no_email(setup):
    c = setup([])
    assert summary.handler({}, None) == {"summariesSent": 0}
    assert c["bedrock"].calls == []
