import io
import os

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")  # clients are built at import; no calls made

import extract  # noqa: E402

TEXTRACT = {"store": "SAVE-ON-FOODS", "date": "2026-09-25", "subtotal": 20.0, "gst": 1.0, "pst": 1.4, "total": 22.4}
EVENT = {"bucket": "b", "key": "uploads/u/r1.jpg", "receiptId": "r1", "analysis": {"fields": TEXTRACT}}


class S3:
    def __init__(self, size=1000):
        self.size = size

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(b"x" * self.size)}


class Bedrock:
    def __init__(self, fields=None, error=None):
        self.fields, self.error, self.calls = fields, error, []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise ClientError({"Error": {"Code": self.error}}, "Converse")
        return {"output": {"message": {"content": [{"toolUse": {"input": self.fields}}]}}}


@pytest.fixture
def use(monkeypatch):
    def _use(s3=None, bedrock=None):
        monkeypatch.setattr(extract, "s3", s3 or S3())
        monkeypatch.setattr(extract, "bedrock", bedrock or Bedrock({}))
        return bedrock
    return _use


def test_model_fields_with_textract_filling_gaps_and_date(use):
    bedrock = use(bedrock=Bedrock({"store": "Save-On-Foods", "date": "2022-09-25", "total": 22.4, "category": "groceries"}))
    out = extract.handler(EVENT, None)
    assert out["source"] == "nova_lite_vision"
    assert out["fields"] == {
        "store": "Save-On-Foods",  # model's reading kept
        "date": "2026-09-25",  # Textract's date wins
        "total": 22.4,
        "category": "groceries",
        "subtotal": 20.0, "gst": 1.0, "pst": 1.4,  # filled from Textract
    }
    content = bedrock.calls[0]["messages"][0]["content"]
    assert content[0]["image"]["format"] == "jpeg"
    assert bedrock.calls[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "record_receipt"}}


def test_model_date_used_when_textract_has_none(use):
    use(bedrock=Bedrock({"store": "A", "date": "2026-10-20", "total": 5, "category": "dining"}))
    event = {**EVENT, "analysis": {"fields": {**TEXTRACT, "date": None}}}
    assert extract.handler(event, None)["fields"]["date"] == "2026-10-20"


def test_unreadable_photo_skips_the_model(use):
    bedrock = use(bedrock=Bedrock({}))
    event = {**EVENT, "analysis": {"unreadable_reason": "BadDocumentException", "fields": {}}}
    assert extract.handler(event, None) == {"source": "textract_only", "fields": {}}
    assert bedrock.calls == []


def test_oversized_image_falls_back_to_textract(use):
    bedrock = use(s3=S3(size=extract.MAX_IMAGE_BYTES + 1), bedrock=Bedrock({}))
    assert extract.handler(EVENT, None) == {"source": "textract_only", "fields": TEXTRACT}
    assert bedrock.calls == []


def test_rejected_image_falls_back_to_textract(use):
    use(bedrock=Bedrock(error="ValidationException"))
    assert extract.handler(EVENT, None)["source"] == "textract_only"


def test_throttling_is_retried(use):
    use(bedrock=Bedrock(error="ThrottlingException"))
    with pytest.raises(extract.RetryableError):
        extract.handler(EVENT, None)


def test_access_denied_fails_the_step(use):
    use(bedrock=Bedrock(error="AccessDeniedException"))
    with pytest.raises(ClientError):
        extract.handler(EVENT, None)
