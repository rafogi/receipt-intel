"""Turn a Textract AnalyzeExpense response into compact fields and OCR text.

Shared by the Analyze Lambda and eval/, so both read Textract the same way.
"""

from __future__ import annotations

from parsing import parse_date, parse_money


def best_field(fields: list[dict], ftype: str) -> tuple[str | None, float, str]:
    """Highest-confidence value Textract assigned to this field type: (text, confidence, printed label)."""
    candidates = [
        f for f in fields
        if f.get("Type", {}).get("Text") == ftype and (f.get("ValueDetection") or {}).get("Text")
    ]
    if not candidates:
        return None, 0.0, ""
    best = max(candidates, key=lambda f: (f.get("ValueDetection") or {}).get("Confidence", 0))
    value = best["ValueDetection"]
    return value["Text"], float(value.get("Confidence", 0.0)), (best.get("LabelDetection") or {}).get("Text", "")


def summary_fields(resp: dict) -> list[dict]:
    return [f for doc in resp.get("ExpenseDocuments", []) for f in doc.get("SummaryFields", [])]


def extract_fields(resp: dict) -> tuple[dict, dict]:
    """Textract's own reading of the key fields, plus its confidence (0-100) in each.

    No category: Textract can't choose one. TAX lines labelled PST go to pst,
    any other tax line to gst (BC receipts don't use HST).
    """
    fields = summary_fields(resp)
    store, store_conf, _ = best_field(fields, "VENDOR_NAME")
    date, date_conf, _ = best_field(fields, "INVOICE_RECEIPT_DATE")
    subtotal, subtotal_conf, _ = best_field(fields, "SUBTOTAL")
    total, total_conf, _ = best_field(fields, "TOTAL")
    out = {
        "store": store.split("\n")[0].strip() if store else None,
        "date": parse_date(date),
        "subtotal": parse_money(subtotal),
        "gst": None,
        "pst": None,
        "total": parse_money(total),
    }
    confidence = {"store": store_conf, "date": date_conf, "subtotal": subtotal_conf, "total": total_conf}
    for f in fields:
        if f.get("Type", {}).get("Text") != "TAX":
            continue
        label = ((f.get("LabelDetection") or {}).get("Text") or "").upper()
        key = "pst" if "PST" in label else "gst"
        if out[key] is None:
            value = f.get("ValueDetection") or {}
            out[key] = parse_money(value.get("Text"))
            confidence[key] = float(value.get("Confidence", 0.0))
    return out, confidence


def ocr_text(resp: dict, max_items: int = 60) -> str:
    """Flatten AnalyzeExpense output into compact lines an LLM can read."""
    lines: list[str] = []
    for doc in resp.get("ExpenseDocuments", []):
        for f in doc.get("SummaryFields", []):
            ftype = f.get("Type", {}).get("Text", "OTHER")
            label = (f.get("LabelDetection") or {}).get("Text", "")
            value = (f.get("ValueDetection") or {}).get("Text", "")
            if value:
                lines.append(f"{ftype}" + (f" [{label}]" if label else "") + f": {value}")
        items = 0
        for group in doc.get("LineItemGroups", []):
            for item in group.get("LineItems", []):
                cells = {
                    x.get("Type", {}).get("Text"): (x.get("ValueDetection") or {}).get("Text", "")
                    for x in item.get("LineItemExpenseFields", [])
                }
                row = cells.get("EXPENSE_ROW") or " ".join(
                    v for k, v in cells.items() if k in ("ITEM", "QUANTITY", "PRICE") and v
                )
                if row and items < max_items:
                    lines.append(f"LINE_ITEM: {row}")
                    items += 1
    return "\n".join(lines)
