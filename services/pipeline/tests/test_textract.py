from textract import extract_fields, ocr_text


def test_extract_fields(expense_response):
    fields, confidence = extract_fields(expense_response)
    assert fields == {
        "store": "SAVE-ON-FOODS",  # first line only, no address
        "date": "2026-09-25",
        "subtotal": 20.00,
        "gst": 1.00,
        "pst": 1.40,
        "total": 22.40,  # highest-confidence TOTAL wins
    }
    assert confidence["total"] == 99.0


def test_extract_fields_empty_response():
    fields, confidence = extract_fields({"ExpenseDocuments": []})
    assert all(v is None for v in fields.values())
    assert confidence["total"] == 0.0


def test_ocr_text(expense_response):
    text = ocr_text(expense_response)
    assert "TOTAL [TOTAL]: $22.40" in text
    assert "LINE_ITEM: MILK 2L 5.49" in text
    assert "LINE_ITEM: BREAD 3.99" in text
