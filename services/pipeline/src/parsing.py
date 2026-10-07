"""Lenient parsing of amounts and dates as they appear on receipts."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

# Month-first formats come before day-first ones: "09/25/26" is the common
# layout on BC till receipts. Truly ambiguous dates (e.g. 03/04/26) resolve
# month-first; the validation step's plausibility window catches bad guesses.
DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%m-%d-%Y",
    "%m-%d-%y",
    "%b %d, %Y",
    "%b %d %Y",
    "%d %b %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
)

_MONEY = re.compile(r"-?\d[\d,]*\.\d{2}")


def parse_money(value) -> float | None:
    """A number, or the first d.dd amount in a string; None if there isn't one."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):  # DynamoDB returns numbers as Decimal
        return round(float(value), 2)
    match = _MONEY.search(str(value))
    return round(float(match.group(0).replace(",", "")), 2) if match else None


def parse_date(value) -> str | None:
    """ISO YYYY-MM-DD, or None if the text isn't a date we recognise."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    # Textract sometimes includes the time, e.g. "09/25/26 18:42".
    text = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?\s*([AaPp][Mm])?$", "", text)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
