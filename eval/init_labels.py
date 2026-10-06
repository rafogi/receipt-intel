"""Create or extend data/labels.csv with one blank row per receipt image.

Safe to re-run after adding photos: existing rows (and your answers) are kept.
"""

from __future__ import annotations

import csv
import sys

from common import IMAGES, LABEL_COLUMNS, LABELS_CSV, list_images


def main() -> int:
    images = list_images()
    if not images:
        IMAGES.mkdir(parents=True, exist_ok=True)
        print(f"No images found. Put receipt photos in: {IMAGES}")
        return 1

    rows: list[dict] = []
    if LABELS_CSV.exists():
        with LABELS_CSV.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    known = {r["image"] for r in rows}

    added = 0
    for path in images:
        if path.name in known:
            continue
        rows.append({col: "" for col in LABEL_COLUMNS} | {"id": path.stem, "image": path.name})
        added += 1

    with LABELS_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=LABEL_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"{LABELS_CSV}: {len(rows)} receipts ({added} new). Fill in the blank columns.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
