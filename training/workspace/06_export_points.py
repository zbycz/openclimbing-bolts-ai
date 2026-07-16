#!/usr/bin/env python3
"""
Step 06: Export annotations from crop_labels to points.json for the Kaggle training kernel.

Output: data/bolt-points/points.json
  { "Foo.jpg": [[cx, cy, radius_px], ...], ... }
  - only images with zero 'undecided' crops are included (i.e. fully reviewed images) —
    an image with any unreviewed crop is skipped entirely, so unconfirmed candidates
    never leak into training as false ground truth.
  - within an included image, only type='bolt' crops become positive points; a fully
    reviewed image with no confirmed bolts is still included, as an empty list (a true
    negative for the tiler).
  - radius_px may be null (kernel uses the default 12px)

Usage:
  python3 06_export_points.py
  python3 06_export_points.py --db data/climbing_paths.sqlite --out data/bolt-points
"""
import argparse
import json
import os
import sqlite3
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/climbing_paths.sqlite")
    ap.add_argument("--out", default="data/bolt-points",
                    help="output directory (default: data/bolt-points)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT image, type, cx, cy, radius_px FROM crop_labels"
    ).fetchall()
    conn.close()

    by_image = defaultdict(list)
    for r in rows:
        by_image[r["image"]].append(r)

    points: dict[str, list] = {}
    n_skipped_undecided = 0
    for image, rs in by_image.items():
        if any(r["type"] == "undecided" for r in rs):
            n_skipped_undecided += 1
            continue
        fname = image.removeprefix("File:")
        points[fname] = [[r["cx"], r["cy"], r["radius_px"]]
                          for r in rs if r["type"] == "bolt"]

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "points.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(points, f, ensure_ascii=False)

    n_bolts = sum(len(v) for v in points.values())
    n_with_r = sum(1 for v in points.values() for p in v if p[2] is not None)
    n_neg_imgs = sum(1 for v in points.values() if not v)
    print(f"Photos: {len(points)} ({n_neg_imgs} pure negatives, "
          f"{n_skipped_undecided} skipped for having unreviewed crops)")
    print(f"Points: {n_bolts} ({n_with_r} with radius_px, {n_bolts - n_with_r} without)")
    print(f"Written: {out_path} ({os.path.getsize(out_path) // 1024} KB)")


if __name__ == "__main__":
    main()
