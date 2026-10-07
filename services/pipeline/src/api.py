"""HTTP API for the phone app: upload URLs, receipt lists and details, manual fixes.

One function behind API Gateway (HTTP API, payload format 2.0). The JWT
authorizer has already verified the Cognito access token; the user id is
always the token's `sub` claim, never a value taken from the request.

    POST  /uploads          presigned POST for a new photo
    GET   /receipts         newest first; ?month=YYYY-MM or ?status=...
    GET   /receipts/{id}    one receipt, with a short-lived photo URL
    PATCH /receipts/{id}    manual fix; saved as source=manual, never overwritten
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config
from botocore.exceptions import ClientError

from receipt_schema import MONEY_FIELDS
from validate import clean_fields, dynamo_update, field_checks

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET = os.environ.get("BUCKET", "")
TABLE = os.environ.get("TABLE", "")
REGION = os.environ.get("AWS_REGION", "us-west-2")
UPLOAD_PREFIX = os.environ.get("UPLOAD_PREFIX", "uploads/")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # Textract's sync limit for images
UPLOAD_URL_SECONDS = 300
PHOTO_URL_SECONDS = 600
CONTENT_TYPES = {"image/jpeg": "jpg", "image/png": "png"}
# ?status= values -> prefix of the byStatus sort key (status#timestamp).
STATUS_PREFIXES = {
    "needs_attention": "needs_",  # needs_review and needs_manual_entry
    "needs_review": "needs_review#",
    "needs_manual_entry": "needs_manual_entry#",
    "processing": "processing#",
    "processed": "processed#",
}
EDITABLE_FIELDS = ["store", "date", *MONEY_FIELDS, "category"]
# Item attribute -> API field. Internal keys (etag, objectKey, index keys) stay private.
PUBLIC_FIELDS = {
    "receiptId": "receiptId",
    "status": "status",
    "reasons": "reasons",
    "store": "store",
    "purchaseDate": "date",
    **{f: f for f in MONEY_FIELDS},
    "category": "category",
    "source": "source",
    "uploadedAt": "uploadedAt",
    "processedAt": "processedAt",
    "editedAt": "editedAt",
}
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Regional endpoint and virtual-hosted style: a presigned URL on the global
# endpoint can redirect for a new bucket, and browsers fail CORS on redirects.
s3 = boto3.client(
    "s3",
    region_name=REGION,
    endpoint_url=f"https://s3.{REGION}.amazonaws.com",
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)
dynamodb = boto3.client("dynamodb", region_name=REGION)
table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE or "unset")
_deserializer = TypeDeserializer()


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


# ---------------------------------------------------------------- routes


def create_upload(user_id: str, event: dict) -> tuple[int, dict]:
    body = parse_body(event)
    content_type = body.get("contentType", "image/jpeg")
    if content_type not in CONTENT_TYPES:
        raise ApiError(400, f"contentType must be one of {sorted(CONTENT_TYPES)}")
    receipt_id = new_receipt_id()
    key = f"{UPLOAD_PREFIX}{user_id}/{receipt_id}.{CONTENT_TYPES[content_type]}"
    # A presigned POST (unlike PUT) lets S3 enforce the size and type.
    upload = s3.generate_presigned_post(
        BUCKET,
        key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, MAX_UPLOAD_BYTES]],
        ExpiresIn=UPLOAD_URL_SECONDS,
    )
    return 201, {"receiptId": receipt_id, "upload": upload, "maxBytes": MAX_UPLOAD_BYTES}


def list_receipts(user_id: str, event: dict) -> tuple[int, dict]:
    params = event.get("queryStringParameters") or {}
    month, status = params.get("month"), params.get("status")
    if month and status:
        raise ApiError(400, "use either month or status, not both")
    try:
        limit = max(1, min(int(params.get("limit", 50)), 100))
    except ValueError:
        raise ApiError(400, "limit must be a number") from None

    query = {"Limit": limit, "ScanIndexForward": False}  # newest first
    if month:
        if not MONTH.match(month):
            raise ApiError(400, "month must be YYYY-MM")
        query["IndexName"] = "byDate"
        query["KeyConditionExpression"] = Key("userId").eq(user_id) & Key("dateKey").begins_with(month)
    elif status:
        if status not in STATUS_PREFIXES:
            raise ApiError(400, f"status must be one of {sorted(STATUS_PREFIXES)}")
        query["IndexName"] = "byStatus"
        query["KeyConditionExpression"] = Key("userId").eq(user_id) & Key("statusKey").begins_with(
            STATUS_PREFIXES[status]
        )
    else:
        # Receipt ids start with a timestamp, so the table's own order is newest-first.
        query["KeyConditionExpression"] = Key("userId").eq(user_id)

    if params.get("nextToken"):
        query["ExclusiveStartKey"] = decode_token(params["nextToken"], user_id)

    resp = table.query(**query)
    out = {"receipts": [public(item) for item in resp.get("Items", [])]}
    if resp.get("LastEvaluatedKey"):
        out["nextToken"] = encode_token(resp["LastEvaluatedKey"])
    return 200, out


def get_receipt(user_id: str, event: dict) -> tuple[int, dict]:
    receipt_id = path_id(event)
    item = table.get_item(Key={"userId": user_id, "receiptId": receipt_id}).get("Item")
    if not item:
        raise ApiError(404, "receipt not found")
    out = public(item)
    photo_key = item.get("objectKey", "")
    if photo_key.startswith(f"{UPLOAD_PREFIX}{user_id}/"):  # never sign another user's object
        out["photoUrl"] = s3.generate_presigned_url(
            "get_object", Params={"Bucket": BUCKET, "Key": photo_key}, ExpiresIn=PHOTO_URL_SECONDS
        )
    return 200, out


def update_receipt(user_id: str, event: dict) -> tuple[int, dict]:
    receipt_id = path_id(event)
    body = parse_body(event)
    unknown = sorted(set(body) - set(EDITABLE_FIELDS))
    if unknown:
        raise ApiError(400, f"unknown fields: {unknown}; editable: {EDITABLE_FIELDS}")
    if not body:
        raise ApiError(400, f"send at least one of {EDITABLE_FIELDS}")

    item = table.get_item(Key={"userId": user_id, "receiptId": receipt_id}).get("Item")
    if not item:
        raise ApiError(404, "receipt not found")

    current = {"store": item.get("store"), "date": item.get("purchaseDate"), "category": item.get("category")}
    current.update({f: item.get(f) for f in MONEY_FIELDS})
    fields = clean_fields({**current, **body})

    # A value that was sent but couldn't be parsed is an error, not a blank.
    reasons = [f"{name}_invalid" for name, value in body.items() if value not in (None, "") and fields[name] is None]
    now = datetime.now(timezone.utc)
    reasons += [r for r in field_checks(fields, now.date()) if r not in reasons]
    if reasons:
        raise ApiError(422, "receipt can't be saved yet", reasons=reasons)

    update = dynamo_update(
        fields, receipt_id, "processed", [], "manual", now.isoformat(),
        extra={"source": "manual", "editedAt": now.isoformat(), "failure": None},
    )
    try:
        resp = dynamodb.update_item(
            TableName=TABLE,
            Key={"userId": {"S": user_id}, "receiptId": {"S": receipt_id}},
            ConditionExpression="attribute_exists(receiptId)",
            ReturnValues="ALL_NEW",
            **update,
        )
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise ApiError(404, "receipt not found") from None
        raise
    saved = {k: _deserializer.deserialize(v) for k, v in resp["Attributes"].items()}
    return 200, public(saved)


ROUTES = {
    "POST /uploads": create_upload,
    "GET /receipts": list_receipts,
    "GET /receipts/{id}": get_receipt,
    "PATCH /receipts/{id}": update_receipt,
}


def handler(event, context):
    route = event.get("routeKey", "")
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt", {}).get("claims", {})
    user_id = claims.get("sub", "")
    try:
        if not user_id:  # every route sits behind the JWT authorizer
            raise ApiError(401, "unauthorized")
        if not SAFE_ID.match(user_id):  # it becomes part of an S3 key
            raise ApiError(403, "unsupported user id")
        if route not in ROUTES:
            raise ApiError(404, "not found")
        status, body = ROUTES[route](user_id, event)
    except ApiError as err:
        status, body = err.status, {"error": err.message, **err.extra}
    except Exception:
        logger.exception("unhandled error", extra={"route": route})
        status, body = 500, {"error": "internal error"}
    logger.info("request", extra={"route": route, "status": status})
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body, default=json_default),
    }


# ---------------------------------------------------------------- helpers


def new_receipt_id() -> str:
    """Sortable by upload time, e.g. 20261007T220516-3f9c2a1b."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


def path_id(event: dict) -> str:
    receipt_id = (event.get("pathParameters") or {}).get("id", "")
    if not SAFE_ID.match(receipt_id):
        raise ApiError(400, "invalid receipt id")
    return receipt_id


def parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(400, "body must be JSON") from None
    if not isinstance(body, dict):
        raise ApiError(400, "body must be a JSON object")
    return body


def public(item: dict) -> dict:
    return {api: item[attr] for attr, api in PUBLIC_FIELDS.items() if item.get(attr) is not None}


def json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def encode_token(last_key: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(last_key, default=json_default).encode()).decode()


def decode_token(token: str, user_id: str) -> dict:
    try:
        key = json.loads(base64.urlsafe_b64decode(token.encode()))
    except (binascii.Error, ValueError):
        raise ApiError(400, "invalid nextToken") from None
    if not isinstance(key, dict) or key.get("userId") != user_id:
        raise ApiError(400, "invalid nextToken")
    return key
