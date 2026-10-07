import sys
from pathlib import Path

# Keep __pycache__ out of src/, which Terraform zips as the Lambda package.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402


def field(ftype: str, value: str, confidence: float = 99.0, label: str = "") -> dict:
    out = {"Type": {"Text": ftype}, "ValueDetection": {"Text": value, "Confidence": confidence}}
    if label:
        out["LabelDetection"] = {"Text": label}
    return out


@pytest.fixture
def expense_response():
    """A small synthetic AnalyzeExpense response (no real receipt data)."""
    return {
        "DocumentMetadata": {"Pages": 1},
        "ExpenseDocuments": [
            {
                "SummaryFields": [
                    field("VENDOR_NAME", "SAVE-ON-FOODS\n#123 Main St", 97.0),
                    field("INVOICE_RECEIPT_DATE", "09/25/26", 95.0),
                    field("SUBTOTAL", "$20.00", 98.0, "SUBTOTAL"),
                    field("TAX", "1.00", 96.0, "GST"),
                    field("TAX", "1.40", 96.0, "PST"),
                    field("TOTAL", "$22.40", 99.0, "TOTAL"),
                    field("TOTAL", "22.00", 40.0, "AMOUNT"),  # lower-confidence duplicate
                ],
                "LineItemGroups": [
                    {
                        "LineItems": [
                            {"LineItemExpenseFields": [field("EXPENSE_ROW", "MILK 2L 5.49")]},
                            {"LineItemExpenseFields": [field("ITEM", "BREAD"), field("PRICE", "3.99")]},
                        ]
                    }
                ],
            }
        ],
    }
