"""Weekly spending summary: EventBridge Scheduler (Mondays) -> this function -> SNS.

Code computes every number (last Monday-Sunday, month to date, budgets); the
Bedrock model only turns those facts into a few readable sentences. The model
never sees receipt text, only category names and amounts, and if the call
fails the plain facts are sent instead.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

import boto3
from boto3.dynamodb.conditions import Key

from spending import budget_progress, month_bounds, receipts_between, summarize, today_local

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TOPIC_ARN = os.environ.get("TOPIC_ARN", "")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-lite-v1:0")
APP_URL = os.environ.get("APP_URL", "")

SYSTEM_PROMPT = (
    "You write a short weekly spending note for one person, as plain text for an email. "
    "Use only the numbers in the JSON you are given; never calculate, estimate or invent figures. "
    "Three to five sentences, friendly and specific: last week's total and its biggest categories, "
    "how the month is going, and any budget at 85% or more. Mention receipts waiting for review if there are any. "
    "No greeting, no sign-off, no markdown."
)

_clients = {}


def clients():
    if not _clients:
        ddb = boto3.resource("dynamodb")
        _clients["receipts"] = ddb.Table(os.environ["TABLE"])
        _clients["budgets"] = ddb.Table(os.environ["BUDGETS_TABLE"])
        _clients["cognito"] = boto3.client("cognito-idp")
        _clients["bedrock"] = boto3.client("bedrock-runtime")
        _clients["sns"] = boto3.client("sns")
    return _clients


def user_ids(cognito) -> list[str]:
    ids = []
    for page in cognito.get_paginator("list_users").paginate(UserPoolId=USER_POOL_ID):
        for user in page["Users"]:
            if user.get("Enabled", True):
                ids += [a["Value"] for a in user.get("Attributes", []) if a["Name"] == "sub"]
    return ids


def last_week(today) -> tuple[str, str]:
    """The Monday-Sunday before the week containing `today`."""
    monday = today - timedelta(days=today.weekday() + 7)
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def facts(table, budgets: list[dict], user_id: str, today) -> dict:
    start, end = last_week(today)
    week = summarize(receipts_between(table, user_id, start, end))
    month = today.strftime("%Y-%m")
    mtd = summarize(receipts_between(table, user_id, month_bounds(month)[0], today.isoformat()))

    def money(entries):
        return [{"category": e["category"], "amount": f"{e['amount']:.2f}"} for e in entries]

    return {
        "lastWeek": {"from": start, "to": end, "total": f"{week['total']:.2f}", "receipts": week["processedCount"],
                     "byCategory": money(week["byCategory"][:4])},
        "monthToDate": {"month": month, "total": f"{mtd['total']:.2f}", "byCategory": money(mtd["byCategory"][:4])},
        "budgets": [{"category": r["category"], "spent": f"{r['spent']:.2f}", "limit": f"{r['limit']:.2f}",
                     "percent": r["percent"]} for r in budget_progress(mtd, budgets)],
        "receiptsWaitingForReview": waiting_count(table, user_id),
    }


def waiting_count(table, user_id: str) -> int:
    """Receipts in needs_review or needs_manual_entry, any date (byStatus index)."""
    query = {
        "IndexName": "byStatus",
        "KeyConditionExpression": Key("userId").eq(user_id) & Key("statusKey").begins_with("needs_"),
        "Select": "COUNT",
    }
    count = 0
    while True:
        resp = table.query(**query)
        count += resp.get("Count", 0)
        if not resp.get("LastEvaluatedKey"):
            return count
        query["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def plain_text(f: dict) -> str:
    w, m = f["lastWeek"], f["monthToDate"]
    lines = [f"Last week ({w['from']} to {w['to']}): ${w['total']} across {w['receipts']} receipts."]
    lines += [f"  {c['category']}: ${c['amount']}" for c in w["byCategory"]]
    lines.append(f"{m['month']} so far: ${m['total']}.")
    lines += [f"  {b['category']} budget: ${b['spent']} of ${b['limit']} ({b['percent']}%)" for b in f["budgets"]]
    if f["receiptsWaitingForReview"]:
        lines.append(f"{f['receiptsWaitingForReview']} receipts are waiting for your review.")
    return "\n".join(lines)


def write_note(bedrock, f: dict) -> str:
    try:
        resp = bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": json.dumps(f)}]}],
            inferenceConfig={"maxTokens": 300, "temperature": 0.3},
        )
        text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"]).strip()
        if text:
            return text + "\n\n" + plain_text(f)  # the exact figures always ride along
    except Exception:
        logger.exception("summary model call failed; sending plain figures")
    return plain_text(f)


def handler(event, context):
    c = clients()
    today = today_local()
    sent = 0
    for user_id in user_ids(c["cognito"]):
        budgets = c["budgets"].query(KeyConditionExpression=Key("userId").eq(user_id)).get("Items", [])
        f = facts(c["receipts"], budgets, user_id, today)
        if f["lastWeek"]["receipts"] == 0 and f["monthToDate"]["total"] == "0.00" and not f["receiptsWaitingForReview"]:
            continue  # nothing to say
        body = write_note(c["bedrock"], f) + (f"\n\nOpen the app: {APP_URL}\n" if APP_URL else "")
        c["sns"].publish(TopicArn=TOPIC_ARN, Subject=f"Your week: ${f['lastWeek']['total']} spent", Message=body)
        sent += 1
    logger.info("weekly summaries sent", extra={"count": sent})
    return {"summariesSent": sent}
