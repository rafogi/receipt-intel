"""Validate step: rule-based checks that decide processed vs. needs_review.

Deliberately not an LLM (ADR-0005): the rules are cheap, deterministic, and
can't be talked into accepting a guessed total. Also builds the DynamoDB update
so the workflow can save the result with a direct UpdateItem call.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from difflib import SequenceMatcher

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer

from parsing import parse_date, parse_money
from receipt_schema import CATEGORIES, MONEY_FIELDS

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MIN_TOTAL_CONFIDENCE = 80.0  # Textract's confidence (0-100) in the printed total
MONEY_TOLERANCE = 0.01  # Textract total vs. extracted total
MATH_TOLERANCE = 0.02  # subtotal + taxes vs. total (rounding on the receipt)
MAX_RECEIPT_AGE = timedelta(days=730)
SAME_STORE_RATIO = 0.8  # OCR misreads ("T&1" for "T&T") still count as the same store

_serializer = TypeSerializer()
_table = None


def receipts_table():
    """The receipts table, or None where there isn't one configured (unit tests)."""
    global _table
    if _table is None and os.environ.get("TABLE"):
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE"])
    return _table


def clean_fields(raw: dict) -> dict:
    """Extraction output -> typed fields (money as floats, ISO date, lower-case category)."""
    category = (raw.get("category") or "").strip().lower() or None
    return {
        "store": (raw.get("store") or "").strip() or None,
        "date": parse_date(raw.get("date")),
        **{f: parse_money(raw.get(f)) for f in MONEY_FIELDS},
        "category": category,
    }


def check(fields: dict, analysis: dict, today: date) -> list[str]:
    """Reasons the receipt needs review; empty means it can be saved as processed."""
    if analysis.get("unreadable_reason"):
        return [f"unreadable:{analysis['unreadable_reason']}"]

    reasons = []
    total = fields["total"]
    if total is not None and total > 0:
        textract_total = (analysis.get("fields") or {}).get("total")
        confidence = (analysis.get("confidence") or {}).get("total", 0.0)
        if textract_total is None or confidence < MIN_TOTAL_CONFIDENCE:
            reasons.append("total_low_confidence")
        elif abs(textract_total - total) > MONEY_TOLERANCE:
            reasons.append("total_mismatch")
    return reasons + field_checks(fields, today)


def field_checks(fields: dict, today: date) -> list[str]:
    """Rules any saved receipt must pass, whether read by the pipeline or typed by the user."""
    reasons = []
    total = fields["total"]
    if total is None or total <= 0:
        reasons.append("total_missing")
    # Math check only where a subtotal is printed; receipts without one
    # (e.g. tax-included totals) are not penalised.
    elif fields["subtotal"] is not None:
        parts = fields["subtotal"] + (fields["gst"] or 0) + (fields["pst"] or 0)
        if abs(parts - total) > MATH_TOLERANCE:
            reasons.append("math_mismatch")

    if not fields["store"]:
        reasons.append("store_missing")
    if not fields["date"]:
        reasons.append("date_missing")
    else:
        purchased = date.fromisoformat(fields["date"])
        if purchased > today + timedelta(days=1) or purchased < today - MAX_RECEIPT_AGE:
            reasons.append("date_implausible")
    if fields["category"] not in CATEGORIES:
        reasons.append("category_missing")
    return reasons


def _store_key(name) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def is_same_receipt(fields: dict, other: dict) -> bool:
    """Same purchase date and total, and the same store when both have one.

    A second photo of a receipt is a different file (new ETag), so the Claim
    step's idempotency can't catch it; this can.
    """
    other_total = parse_money(other.get("total"))
    if fields["total"] is None or other_total is None or abs(fields["total"] - other_total) > MONEY_TOLERANCE:
        return False
    if fields["date"] != other.get("purchaseDate"):
        return False
    mine, theirs = _store_key(fields["store"]), _store_key(other.get("store"))
    return not mine or not theirs or SequenceMatcher(None, mine, theirs).ratio() >= SAME_STORE_RATIO


def find_duplicate(table, user_id: str, receipt_id: str, fields: dict) -> str | None:
    """Id of an existing receipt this one appears to duplicate, if any."""
    if table is None or not fields["date"] or fields["total"] is None:
        return None
    resp = table.query(
        IndexName="byDate",
        KeyConditionExpression=Key("userId").eq(user_id) & Key("dateKey").begins_with(f"{fields['date']}#"),
    )
    for other in resp.get("Items", []):
        if other.get("receiptId") != receipt_id and is_same_receipt(fields, other):
            return other["receiptId"]
    return None


def dynamo_update(
    fields: dict, receipt_id: str, status: str, reasons: list[str], source: str, now: str, extra: dict | None = None
) -> dict:
    """UpdateItem arguments: SET known fields, REMOVE missing ones (stale on reprocessing).

    `extra` adds more attributes the same way (a None value removes it). All
    attribute names go through placeholders, so reserved words (status,
    source, date, ...) never break the expression.
    """
    values = {
        "status": status,
        "statusKey": f"{status}#{now}",  # sort key of the byStatus index
        "reasons": reasons,
        "extractionSource": source,
        "processedAt": now,
        "store": fields["store"],
        "purchaseDate": fields["date"],
        "dateKey": f"{fields['date']}#{receipt_id}" if fields["date"] else None,  # byDate index
        "category": fields["category"],
        **{f: Decimal(str(fields[f])) if fields[f] is not None else None for f in MONEY_FIELDS},
        **(extra or {}),
    }
    names, attr_values, sets, removes = {}, {}, [], []
    for i, (name, value) in enumerate(values.items()):
        names[f"#a{i}"] = name
        if value is None:
            removes.append(f"#a{i}")
        else:
            sets.append(f"#a{i} = :v{i}")
            attr_values[f":v{i}"] = _serializer.serialize(value)
    expression = "SET " + ", ".join(sets) + (" REMOVE " + ", ".join(removes) if removes else "")
    return {
        "UpdateExpression": expression,
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": attr_values,
    }


def handler(event, context):
    analysis = event["analysis"]
    extraction = event["extraction"]
    fields = clean_fields(extraction.get("fields") or {})
    now = datetime.now(timezone.utc)
    reasons = check(fields, analysis, now.date())
    duplicate_of = find_duplicate(receipts_table(), event["userId"], event["receiptId"], fields)
    if duplicate_of:
        reasons.append("possible_duplicate")
    status = "needs_review" if reasons else "processed"
    update = dynamo_update(
        fields, event["receiptId"], status, reasons, extraction.get("source", "unknown"), now.isoformat(),
        extra={"duplicateOf": duplicate_of},  # None removes a stale value on reprocessing
    )
    logger.info("validated receipt", extra={"receipt_id": event["receiptId"], "status": status, "reasons": reasons})
    return {"status": status, "reasons": reasons, "fields": fields, "update": update}
