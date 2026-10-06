"""Analyze step: run Textract AnalyzeExpense on an uploaded receipt photo.

The workflow calls this Lambda instead of integrating Textract directly because a
full AnalyzeExpense response can exceed the 256 KB Step Functions payload limit
(ADR-0004). The raw response is saved to S3; only compact fields go back.
"""

from __future__ import annotations

import json
import logging
import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from textract import extract_fields, ocr_text

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_config = Config(retries={"mode": "standard", "max_attempts": 3})
textract = boto3.client("textract", config=_config)
s3 = boto3.client("s3", config=_config)

RAW_PREFIX = os.environ.get("RAW_PREFIX", "textract/")

# The photo is the problem, not the system: route to review, don't retry.
UNREADABLE_ERRORS = {
    "BadDocumentException",
    "UnsupportedDocumentException",
    "DocumentTooLargeException",
    "InvalidS3ObjectException",
}
# Worth retrying with backoff (the workflow's Retry matches RetryableError).
TRANSIENT_ERRORS = {
    "ThrottlingException",
    "ProvisionedThroughputExceededException",
    "InternalServerError",
    "LimitExceededException",
}


class RetryableError(Exception):
    """Transient failure; Step Functions retries the step with backoff."""


def handler(event, context):
    bucket, key = event["bucket"], event["key"]
    user_id, receipt_id = event["userId"], event["receiptId"]
    try:
        resp = textract.analyze_expense(Document={"S3Object": {"Bucket": bucket, "Name": key}})
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "")
        if code in UNREADABLE_ERRORS:
            logger.info("unreadable receipt", extra={"receipt_id": receipt_id, "code": code})
            return {"unreadable_reason": code, "fields": {}, "confidence": {}, "ocr_text": "", "raw_key": None}
        if code in TRANSIENT_ERRORS:
            raise RetryableError(code) from err
        raise

    resp.pop("ResponseMetadata", None)
    raw_key = f"{RAW_PREFIX}{user_id}/{receipt_id}.json"
    s3.put_object(Bucket=bucket, Key=raw_key, Body=json.dumps(resp).encode(), ContentType="application/json")

    fields, confidence = extract_fields(resp)
    logger.info("analyzed receipt", extra={"receipt_id": receipt_id, "pages": resp.get("DocumentMetadata", {}).get("Pages")})
    return {
        "unreadable_reason": None,
        "fields": fields,
        "confidence": confidence,
        "ocr_text": ocr_text(resp),
        "raw_key": raw_key,
    }
