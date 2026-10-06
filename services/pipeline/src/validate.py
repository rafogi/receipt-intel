"""Validate step: rule-based checks that decide processed vs. needs_review.

Deliberately not an LLM (ADR-0005): the rules are cheap, deterministic, and
can't be talked into accepting a guessed total. Also builds the DynamoDB update
so the workflow can save the result with a direct UpdateItem call.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from boto3.dynamodb.types import TypeSerializer

from parsing import parse_date, parse_money
from receipt_schema import CATEGORIES, MONEY_FIELDS

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MIN_TOTAL_CONFIDENCE = 80.0  # Textract's confidence (0-100) in the printed total
MONEY_TOLERANCE = 0.01  # Textract total vs. extracted total
MATH_TOLERANCE = 0.02  # subtotal + taxes vs. total (rounding on the receipt)
MAX_RECEIPT_AGE = timedelta(days=730)

_serializer = TypeSerializer()


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
    if total is None or total <= 0:
        reasons.append("total_missing")
    else:
        textract_total = (analysis.get("fields") or {}).get("total")
        confidence = (analysis.get("confidence") or {}).get("total", 0.0)
        if textract_total is None or confidence < MIN_TOTAL_CONFIDENCE:
            reasons.append("total_low_confidence")
        elif abs(textract_total - total) > MONEY_TOLERANCE:
            reasons.append("total_mismatch")
        # Math check only where a subtotal is printed; receipts without one
        # (e.g. tax-included totals) are not penalised.
        if fields["subtotal"] is not None:
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


def dynamo_update(fields: dict, receipt_id: str, status: str, reasons: list[str], source: str, now: str) -> dict:
    """UpdateItem arguments: SET known fields, REMOVE missing ones (stale on reprocessing).

    All attribute names go through placeholders, so reserved words (status,
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
    status = "needs_review" if reasons else "processed"
    update = dynamo_update(
        fields, event["receiptId"], status, reasons, extraction.get("source", "unknown"), now.isoformat()
    )
    logger.info("validated receipt", extra={"receipt_id": event["receiptId"], "status": status, "reasons": reasons})
    return {"status": status, "reasons": reasons, "fields": fields, "update": update}
