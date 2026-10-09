import json
from decimal import Decimal

import pytest
from boto3.dynamodb.conditions import ConditionExpressionBuilder
from boto3.dynamodb.types import TypeSerializer

import api

USER = "1a2b3c4d-0000-4000-8000-000000000001"


class FakeTable:
    def __init__(self, items=()):
        self.items = {(i["userId"], i["receiptId"]): i for i in items}
        self.queries = []

    def get_item(self, Key):
        item = self.items.get((Key["userId"], Key["receiptId"]))
        return {"Item": item} if item else {}

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"Items": list(self.items.values()), "LastEvaluatedKey": {"userId": USER, "receiptId": "r1"}}


class FakeDynamo:
    def __init__(self):
        self.updates = []

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        names, values = kwargs["ExpressionAttributeNames"], kwargs["ExpressionAttributeValues"]
        attrs = {"userId": {"S": USER}, "receiptId": kwargs["Key"]["receiptId"]}
        for placeholder, name in names.items():
            value = values.get(placeholder.replace("#a", ":v"))
            if value is not None:
                attrs[name] = value
        return {"Attributes": attrs}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # Presigning is local, but botocore still wants some credentials. A client
    # resolves them when it's created, so build a fresh one after setting them.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.setattr(api, "s3", api.make_s3_client())
    monkeypatch.setattr(api, "BUCKET", "receipts-bucket")
    monkeypatch.setattr(api, "TABLE", "receipts-table")


def receipt(**overrides):
    return {
        "userId": USER,
        "receiptId": "20261007T220516-3f9c2a1b",
        "status": "needs_review",
        "reasons": ["category_missing"],
        "store": "T&T Supermarket",
        "purchaseDate": "2026-09-29",
        "total": Decimal("8.5"),
        "objectKey": f"uploads/{USER}/20261007T220516-3f9c2a1b.jpg",
        "etag": "abc",
        "dateKey": "2026-09-29#20261007T220516-3f9c2a1b",
        **overrides,
    }


def call(route, user=USER, body=None, path_id=None, query=None):
    event = {
        "routeKey": route,
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": user}}}} if user else {},
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": {"id": path_id} if path_id else None,
        "queryStringParameters": query,
    }
    resp = api.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"])


def key_condition(query_kwargs):
    built = ConditionExpressionBuilder().build_expression(query_kwargs["KeyConditionExpression"], is_key_condition=True)
    return built.condition_expression, built.attribute_value_placeholders


# ---------------------------------------------------------------- routing and auth


def test_missing_claims_is_401():
    assert call("GET /receipts", user=None)[0] == 401


def test_unknown_route_is_404():
    assert call("PUT /budgets")[0] == 404


def test_unexpected_user_id_is_rejected():
    assert call("POST /uploads", user="../other")[0] == 403


# ---------------------------------------------------------------- uploads


def test_create_upload_presigned_post():
    status, body = call("POST /uploads", body={"contentType": "image/jpeg"})
    assert status == 201
    fields = body["upload"]["fields"]
    assert fields["key"] == f"uploads/{USER}/{body['receiptId']}.jpg"
    assert fields["Content-Type"] == "image/jpeg"
    assert body["upload"]["url"] == "https://receipts-bucket.s3.us-west-2.amazonaws.com/"
    policy = json.loads(__import__("base64").b64decode(fields["policy"]))
    assert ["content-length-range", 1, api.MAX_UPLOAD_BYTES] in policy["conditions"]
    assert api.SAFE_ID.match(body["receiptId"])  # the pipeline's key check accepts it


def test_create_upload_rejects_other_types():
    assert call("POST /uploads", body={"contentType": "application/pdf"})[0] == 400


# ---------------------------------------------------------------- lists


def test_list_default_is_newest_first(monkeypatch):
    table = FakeTable([receipt()])
    monkeypatch.setattr(api, "table", table)
    status, body = call("GET /receipts")
    assert status == 200
    q = table.queries[0]
    assert "IndexName" not in q and q["ScanIndexForward"] is False
    item = body["receipts"][0]
    assert item["date"] == "2026-09-29" and item["total"] == 8.5
    assert "etag" not in item and "objectKey" not in item and "dateKey" not in item
    assert body["nextToken"]


def test_list_by_month_uses_date_index(monkeypatch):
    table = FakeTable([receipt()])
    monkeypatch.setattr(api, "table", table)
    assert call("GET /receipts", query={"month": "2026-09"})[0] == 200
    q = table.queries[0]
    assert q["IndexName"] == "byDate"
    _, values = key_condition(q)
    assert set(values.values()) == {USER, "2026-09"}


def test_list_needs_attention_uses_status_prefix(monkeypatch):
    table = FakeTable([receipt()])
    monkeypatch.setattr(api, "table", table)
    assert call("GET /receipts", query={"status": "needs_attention"})[0] == 200
    q = table.queries[0]
    assert q["IndexName"] == "byStatus"
    _, values = key_condition(q)
    assert "needs_" in values.values()


@pytest.mark.parametrize(
    "query",
    [
        {"month": "2026-13"},
        {"status": "deleted"},
        {"month": "2026-09", "status": "processed"},
        {"limit": "lots"},
        {"nextToken": "not-base64!"},
    ],
)
def test_list_rejects_bad_parameters(monkeypatch, query):
    monkeypatch.setattr(api, "table", FakeTable())
    assert call("GET /receipts", query=query)[0] == 400


def test_next_token_is_bound_to_the_user(monkeypatch):
    monkeypatch.setattr(api, "table", FakeTable())
    other_users_token = api.encode_token({"userId": "someone-else", "receiptId": "r1"})
    assert call("GET /receipts", query={"nextToken": other_users_token})[0] == 400
    own_token = api.encode_token({"userId": USER, "receiptId": "r1"})
    assert call("GET /receipts", query={"nextToken": own_token})[0] == 200


# ---------------------------------------------------------------- detail


def test_get_receipt_includes_photo_url(monkeypatch):
    monkeypatch.setattr(api, "table", FakeTable([receipt()]))
    status, body = call("GET /receipts/{id}", path_id="20261007T220516-3f9c2a1b")
    assert status == 200
    assert body["photoUrl"].startswith("https://receipts-bucket.s3.us-west-2.amazonaws.com/uploads/")
    assert body["reasons"] == ["category_missing"]


def test_get_receipt_never_signs_another_users_object(monkeypatch):
    monkeypatch.setattr(api, "table", FakeTable([receipt(objectKey="uploads/someone-else/x.jpg")]))
    _, body = call("GET /receipts/{id}", path_id="20261007T220516-3f9c2a1b")
    assert "photoUrl" not in body


def test_get_missing_receipt_is_404(monkeypatch):
    monkeypatch.setattr(api, "table", FakeTable())
    assert call("GET /receipts/{id}", path_id="nope")[0] == 404


def test_get_rejects_unsafe_id():
    assert call("GET /receipts/{id}", path_id="../../x")[0] == 400


# ---------------------------------------------------------------- manual fix


def test_patch_completes_a_flagged_receipt(monkeypatch):
    monkeypatch.setattr(api, "table", FakeTable([receipt()]))
    dynamo = FakeDynamo()
    monkeypatch.setattr(api, "dynamodb", dynamo)
    status, body = call("PATCH /receipts/{id}", path_id="20261007T220516-3f9c2a1b", body={"category": "Groceries"})
    assert status == 200, body
    assert body["status"] == "processed" and body["source"] == "manual" and body["category"] == "groceries"

    update = dynamo.updates[0]
    assert update["ConditionExpression"] == "attribute_exists(receiptId)"
    names = update["ExpressionAttributeNames"]
    values = update["ExpressionAttributeValues"]
    by_name = {name: values.get(p.replace("#a", ":v")) for p, name in names.items()}
    assert by_name["source"] == TypeSerializer().serialize("manual")
    assert by_name["failure"] is None  # removed: an old pipeline failure no longer applies
    assert by_name["duplicateOf"] is None  # saving confirms it isn't a duplicate
    assert by_name["total"] == {"N": "8.5"}  # unchanged fields are kept


def test_patch_reports_what_is_still_missing(monkeypatch):
    monkeypatch.setattr(api, "table", FakeTable([receipt()]))
    status, body = call("PATCH /receipts/{id}", path_id="20261007T220516-3f9c2a1b", body={"store": "T&T"})
    assert status == 422
    assert body["reasons"] == ["category_missing"]


def test_patch_flags_unparseable_values(monkeypatch):
    monkeypatch.setattr(api, "table", FakeTable([receipt()]))
    status, body = call(
        "PATCH /receipts/{id}", path_id="20261007T220516-3f9c2a1b", body={"total": "abc", "category": "groceries"}
    )
    assert status == 422
    assert body["reasons"] == ["total_invalid"]  # not also total_missing


def test_patch_rejects_unknown_fields(monkeypatch):
    monkeypatch.setattr(api, "table", FakeTable([receipt()]))
    status, body = call("PATCH /receipts/{id}", path_id="20261007T220516-3f9c2a1b", body={"status": "processed"})
    assert status == 400
    assert "status" in body["error"]


def test_patch_missing_receipt_is_404(monkeypatch):
    monkeypatch.setattr(api, "table", FakeTable())
    assert call("PATCH /receipts/{id}", path_id="nope", body={"category": "gas"})[0] == 404


# ---------------------------------------------------------------- delete


class FakeS3:
    def __init__(self):
        self.deleted = []

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


def test_delete_removes_item_photo_and_raw_textract(monkeypatch):
    table = FakeTable([receipt()])
    table.delete_item = lambda Key: table.items.pop((Key["userId"], Key["receiptId"]))
    s3 = FakeS3()
    monkeypatch.setattr(api, "table", table)
    monkeypatch.setattr(api, "s3", s3)
    status, body = call("DELETE /receipts/{id}", path_id="20261007T220516-3f9c2a1b")
    assert status == 200 and body == {"deleted": "20261007T220516-3f9c2a1b"}
    assert not table.items
    assert s3.deleted == [
        f"textract/{USER}/20261007T220516-3f9c2a1b.json",
        f"uploads/{USER}/20261007T220516-3f9c2a1b.jpg",
    ]


def test_delete_never_touches_another_users_object(monkeypatch):
    table = FakeTable([receipt(objectKey="uploads/someone-else/x.jpg")])
    table.delete_item = lambda Key: None
    s3 = FakeS3()
    monkeypatch.setattr(api, "table", table)
    monkeypatch.setattr(api, "s3", s3)
    assert call("DELETE /receipts/{id}", path_id="20261007T220516-3f9c2a1b")[0] == 200
    assert all(key.startswith(f"textract/{USER}/") for key in s3.deleted)


def test_delete_missing_receipt_is_404(monkeypatch):
    monkeypatch.setattr(api, "table", FakeTable())
    assert call("DELETE /receipts/{id}", path_id="nope")[0] == 404
