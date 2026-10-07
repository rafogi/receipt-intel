"""Paths, image preparation, and lenient parsing of hand-entered labels."""

from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

try:  # iPhone photos are often HEIC; this makes Pillow able to open them.
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pass

EVAL_DIR = Path(__file__).resolve().parent
DATA = EVAL_DIR / "data"  # gitignored: receipts contain personal information
IMAGES = DATA / "images"
LABELS_CSV = DATA / "labels.csv"
CACHE = DATA / "cache"
RESULTS = DATA / "results"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}

LABEL_COLUMNS = [
    "id",
    "image",
    "store",
    "date",
    "subtotal",
    "gst",
    "pst",
    "total",
    "category",
    "difficulty",
    "notes",
]


def list_images() -> list[Path]:
    if not IMAGES.exists():
        return []
    return sorted(p for p in IMAGES.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)


def prepare_image(path: Path, max_side: int = 2000) -> bytes:
    """Rotate per EXIF, downscale, and re-encode as JPEG.

    Both approaches get the identical bytes so the comparison is fair, and the
    smaller size keeps Textract under its sync limit and Bedrock image tokens low.
    """
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((max_side, max_side))
        for quality in (90, 80, 70):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() < 4_500_000:
                return buf.getvalue()
    return buf.getvalue()


def parse_money(value: str | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        return None
    return round(float(text), 2)


def parse_date(value: str | None) -> str | None:
    """Accept ISO dates, plus the M/D/YYYY that Excel sometimes rewrites them to."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date {text!r}; use YYYY-MM-DD")


def normalize_store(name: str | None) -> str:
    if not name:
        return ""
    text = name.lower()
    text = re.sub(r"#\s*\d+", " ", text)  # store numbers
    text = re.sub(r"\b(inc|ltd|llc|corp|co|store|the)\b", " ", text)
    return re.sub(r"[^a-z0-9]", "", text)
