#!/usr/bin/env python3
"""
Step 06: Export annotations from crop_labels to points.json for the Kaggle training kernel.

Output: data/bolt-points/points.json
  { "Foo.jpg": [[cx, cy, radius_px], ...], ... }
  - an image is included if it has at least one reviewed crop (type='bolt' or
    'no-bolt'); images that are completely untouched (only 'undecided' crops) are
    skipped, since we can't yet say anything about them.
  - only type='bolt' crops become positive points. 'undecided' crops (e.g. freshly
    seeded candidates in an otherwise-reviewed photo) are neither a positive nor a
    negative — they're left out of labels.txt entirely rather than being asserted
    as ground truth in either direction.
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
    n_skipped_untouched = 0
    for image, rs in by_image.items():
        if not any(r["type"] in ("bolt", "no-bolt") for r in rs):
            n_skipped_untouched += 1  # nobody has reviewed this photo at all yet
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
          f"{n_skipped_untouched} skipped for being completely unreviewed)")
    print(f"Points: {n_bolts} ({n_with_r} with radius_px, {n_bolts - n_with_r} without)")
    print(f"Written: {out_path} ({os.path.getsize(out_path) // 1024} KB)")


if __name__ == "__main__":
    main()
