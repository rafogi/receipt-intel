from datetime import date

import pytest

from validate import check, clean_fields, dynamo_update, handler

TODAY = date(2026, 10, 1)


def analysis(total=22.40, confidence=99.0, unreadable=None):
    return {"unreadable_reason": unreadable, "fields": {"total": total}, "confidence": {"total": confidence}}


def good_fields(**overrides):
    raw = {
        "store": "Save-On-Foods",
        "date": "2026-09-25",
        "subtotal": 20.00,
        "gst": 1.00,
        "pst": 1.40,
        "total": 22.40,
        "category": "groceries",
        **overrides,
    }
    return clean_fields(raw)


def test_good_receipt_passes():
    assert check(good_fields(), analysis(), TODAY) == []


def test_receipt_without_subtotal_passes():
    # No subtotal printed: the math check is skipped, not failed.
    assert check(good_fields(subtotal=None, gst=None, pst=None), analysis(), TODAY) == []


@pytest.mark.parametrize(
    "overrides, analysis_kwargs, reason",
    [
        ({"total": None}, {}, "total_missing"),
        ({}, {"confidence": 60.0}, "total_low_confidence"),
        ({}, {"total": None}, "total_low_confidence"),
        ({"total": 24.40, "subtotal": 22.00}, {}, "total_mismatch"),
        ({"subtotal": 25.00}, {}, "math_mismatch"),
        ({"store": " "}, {}, "store_missing"),
        ({"date": None}, {}, "date_missing"),
        ({"date": "2016-09-25"}, {}, "date_implausible"),  # the 2-digit-year misread
        ({"date": "2026-12-25"}, {}, "date_implausible"),
        ({"category": None}, {}, "category_missing"),
        ({"category": "crypto"}, {}, "category_missing"),
    ],
)
def test_rules(overrides, analysis_kwargs, reason):
    assert reason in check(good_fields(**overrides), analysis(**analysis_kwargs), TODAY)


def test_unreadable_short_circuits():
    reasons = check(good_fields(), analysis(unreadable="BadDocumentException"), TODAY)
    assert reasons == ["unreadable:BadDocumentException"]


def test_dynamo_update_sets_and_removes():
    fields = good_fields(gst=None, pst=None, subtotal=None)
    update = dynamo_update(fields, "r1", "processed", [], "textract_only", "2026-10-01T00:00:00+00:00")
    names = update["ExpressionAttributeNames"]
    values = update["ExpressionAttributeValues"]
    by_name = {names[k]: values.get(k.replace("#a", ":v")) for k in names}

    assert by_name["status"] == {"S": "processed"}
    assert by_name["dateKey"] == {"S": "2026-09-25#r1"}
    assert by_name["total"] == {"N": "22.4"}
    assert by_name["reasons"] == {"L": []}
    assert " REMOVE " in update["UpdateExpression"]
    for missing in ("subtotal", "gst", "pst"):
        assert by_name[missing] is None  # removed, so no value placeholder
    # Reserved words never appear bare in the expression.
    assert "status" not in update["UpdateExpression"]


def test_handler_textract_only_goes_to_review():
    """The Phase 2 placeholder (Textract fields, no category) always needs review."""
    textract_fields = {"store": "SAVE-ON-FOODS", "date": "2026-09-25", "total": 22.40}
    event = {
        "userId": "u1",
        "receiptId": "r1",
        "analysis": analysis(),
        "extraction": {"source": "textract_only", "fields": textract_fields},
    }
    result = handler(event, None)
    assert result["status"] == "needs_review"
    assert "category_missing" in result["reasons"]
