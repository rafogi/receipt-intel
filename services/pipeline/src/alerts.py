"""Budget alerts: DynamoDB Streams on the receipts table -> this function -> SNS.

The event source mapping only passes records whose new image is `processed`
(pipeline result or a manual fix). For each user and month touched, recompute
the month's spending and alert once per budget, month and threshold. Streams
deliver at least once, so the "already alerted" mark is a conditional update
made before publishing: a retry can't send the same alert twice.
"""

from __future__ import annotations

import logging
import os

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from spending import budget_progress, crossed_threshold, month_bounds, receipts_between, summarize

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TOPIC_ARN = os.environ.get("TOPIC_ARN", "")
APP_URL = os.environ.get("APP_URL", "")

_resources = {}


def tables():
    if not _resources:
        ddb = boto3.resource("dynamodb")
        _resources["receipts"] = ddb.Table(os.environ["TABLE"])
        _resources["budgets"] = ddb.Table(os.environ["BUDGETS_TABLE"])
        _resources["sns"] = boto3.client("sns")
    return _resources


def touched(records: list[dict]) -> set[tuple[str, str]]:
    """(userId, YYYY-MM) pairs from stream records with a processed new image."""
    out = set()
    for rec in records:
        image = (rec.get("dynamodb") or {}).get("NewImage") or {}
        user = image.get("userId", {}).get("S")
        day = image.get("purchaseDate", {}).get("S")
        if user and day and image.get("status", {}).get("S") == "processed":
            out.add((user, day[:7]))
    return out


def alerts_due(progress: list[dict], budgets: list[dict], month: str) -> list[tuple[dict, int]]:
    """(row, threshold) for budgets that reached a threshold not yet alerted this month."""
    alerted = {b["category"]: int((b.get("alerts") or {}).get(month, 0)) for b in budgets}
    due = []
    for row in progress:
        threshold = crossed_threshold(row["percent"])
        if threshold > alerted.get(row["category"], 0):
            due.append((row, threshold))
    return due


def message(row: dict, threshold: int, month: str) -> tuple[str, str]:
    name = "Overall" if row["category"] == "total" else row["category"].capitalize()
    verb = "over" if row["percent"] >= 100 else "at"
    subject = f"{name} budget {verb} {row['percent']}% for {month}"
    body = (
        f"{name}: ${row['spent']:,.2f} of ${row['limit']:,.2f} spent in {month} ({row['percent']}%).\n"
        + ("You've reached the budget.\n" if threshold >= 100 else "You're close to the budget.\n")
        + (f"\nOpen the app: {APP_URL}/?view=month&month={month}\n" if APP_URL else "")
    )
    return subject, body


def mark_alerted(budgets_table, user_id: str, category: str, month: str, threshold: int) -> bool:
    """Record the alert; False if this threshold (or a higher one) was already recorded."""
    try:
        budgets_table.update_item(
            Key={"userId": user_id, "category": category},
            UpdateExpression="SET alerts = if_not_exists(alerts, :empty)",
            ConditionExpression="attribute_exists(category)",
            ExpressionAttributeValues={":empty": {}},
        )
        budgets_table.update_item(
            Key={"userId": user_id, "category": category},
            UpdateExpression="SET alerts.#m = :t",
            ConditionExpression="attribute_not_exists(alerts.#m) OR alerts.#m < :t",
            ExpressionAttributeNames={"#m": month},
            ExpressionAttributeValues={":t": threshold},
        )
        return True
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False  # already alerted, or the budget was deleted meanwhile
        raise


def unmark(budgets_table, user_id: str, category: str, month: str, budgets: list[dict]) -> None:
    previous = next((int((b.get("alerts") or {}).get(month, 0)) for b in budgets if b["category"] == category), 0)
    budgets_table.update_item(
        Key={"userId": user_id, "category": category},
        UpdateExpression="SET alerts.#m = :p" if previous else "REMOVE alerts.#m",
        ExpressionAttributeNames={"#m": month},
        **({"ExpressionAttributeValues": {":p": previous}} if previous else {}),
    )


def handler(event, context):
    res = tables()
    sent = 0
    for user_id, month in sorted(touched(event.get("Records", []))):
        budgets = res["budgets"].query(KeyConditionExpression=Key("userId").eq(user_id)).get("Items", [])
        if not budgets:
            continue
        summary = summarize(receipts_between(res["receipts"], user_id, *month_bounds(month)))
        for row, threshold in alerts_due(budget_progress(summary, budgets), budgets, month):
            if not mark_alerted(res["budgets"], user_id, row["category"], month, threshold):
                continue
            subject, body = message(row, threshold, month)
            try:
                # One topic for this personal app; per-user delivery would need each user's address.
                res["sns"].publish(TopicArn=TOPIC_ARN, Subject=subject[:100], Message=body)
            except Exception:
                # Undo the mark so the stream's retry sends it, rather than losing the alert.
                unmark(res["budgets"], user_id, row["category"], month, budgets)
                raise
            sent += 1
            logger.info("budget alert", extra={"category": row["category"], "month": month, "threshold": threshold})
    return {"alertsSent": sent}
