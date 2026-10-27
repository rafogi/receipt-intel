"""Extract step: Nova Lite reads the receipt photo (nova_lite_vision in eval/).

A Lambda rather than a direct Bedrock integration because the model needs the
image bytes, which Step Functions can't read from S3 into a Converse request.
Textract (the Analyze step) still runs: Validate cross-checks the model's total
against it, and Textract's reading fills any field the model leaves out.
"""

from __future__ import annotations

import logging
import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from receipt_schema import SYSTEM_PROMPT, TOOL_SPEC, vision_user_prompt

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_config = Config(retries={"mode": "standard", "max_attempts": 3})
s3 = boto3.client("s3", config=_config)
bedrock = boto3.client("bedrock-runtime", config=_config)

MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-lite-v1:0")
MAX_IMAGE_BYTES = 3_750_000  # Converse image limit
IMAGE_FORMATS = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png"}
FALLBACK_FIELDS = ["store", "date", "subtotal", "gst", "pst", "total"]
# Textract read the date on 6/8 test receipts, Nova Lite on 4/8 (wrong years,
# 2026-10-21 eval), so Textract's date wins when it found one.
PREFER_TEXTRACT = ["date"]

TRANSIENT_ERRORS = {
    "ThrottlingException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelNotReadyException",
    "ModelTimeoutException",
}


class RetryableError(Exception):
    """Transient failure; Step Functions retries the step with backoff."""


def textract_only(textract_fields: dict) -> dict:
    return {"source": "textract_only", "fields": textract_fields}


def read_receipt(image: bytes, image_format: str) -> dict:
    resp = bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [
            {"image": {"format": image_format, "source": {"bytes": image}}},
            {"text": vision_user_prompt()},
        ]}],
        toolConfig={"tools": [TOOL_SPEC], "toolChoice": {"tool": {"name": "record_receipt"}}},
        inferenceConfig={"maxTokens": 800, "temperature": 0},
    )
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            return block["toolUse"]["input"] or {}
    return {}


def merge(model_fields: dict, textract_fields: dict) -> dict:
    """The model's reading, with Textract's for any printed field the model left out
    (and for the date, whenever Textract found one)."""
    out = dict(model_fields)
    for f in FALLBACK_FIELDS:
        missing = out.get(f) in (None, "")
        if textract_fields.get(f) is not None and (missing or f in PREFER_TEXTRACT):
            out[f] = textract_fields[f]
    return out


def handler(event, context):
    analysis = event.get("analysis") or {}
    textract_fields = analysis.get("fields") or {}
    receipt_id = event["receiptId"]
    if analysis.get("unreadable_reason"):
        return textract_only(textract_fields)  # Validate routes it to review anyway

    image_format = IMAGE_FORMATS.get(event["key"].rsplit(".", 1)[-1].lower())
    image = s3.get_object(Bucket=event["bucket"], Key=event["key"])["Body"].read()
    if not image_format or len(image) > MAX_IMAGE_BYTES:
        logger.info("image not sent to model", extra={"receipt_id": receipt_id, "bytes": len(image)})
        return textract_only(textract_fields)

    try:
        model_fields = read_receipt(image, image_format)
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "")
        if code in TRANSIENT_ERRORS:
            raise RetryableError(code) from err
        if code == "ValidationException":  # the model rejected this image
            logger.info("model rejected image", extra={"receipt_id": receipt_id})
            return textract_only(textract_fields)
        raise

    logger.info("extracted receipt", extra={"receipt_id": receipt_id, "model": MODEL_ID})
    return {"source": "nova_lite_vision", "fields": merge(model_fields, textract_fields)}
