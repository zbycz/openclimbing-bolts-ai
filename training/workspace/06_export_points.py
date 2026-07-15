#!/usr/bin/env python3
"""
Step 06: Export annotations from crop_labels to points.json for the Kaggle training kernel.

Output: data/bolt-points/points.json
  { "Foo.jpg": [[cx, cy, radius_px], ...], ... }
  - only type='bolt' or type='undecided' (no-bolt entries are skipped)
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
        "SELECT image, cx, cy, radius_px "
        "FROM crop_labels "
        "WHERE type IN ('bolt', 'undecided')"
    ).fetchall()
    conn.close()

    points: dict[str, list] = defaultdict(list)
    for r in rows:
        fname = r["image"].removeprefix("File:")
        points[fname].append([r["cx"], r["cy"], r["radius_px"]])

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "points.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dict(points), f, ensure_ascii=False)

    n_bolts = sum(len(v) for v in points.values())
    n_with_r = sum(1 for v in points.values() for p in v if p[2] is not None)
    print(f"Photos: {len(points)}")
    print(f"Points: {n_bolts} ({n_with_r} with radius_px, {n_bolts - n_with_r} without)")
    print(f"Written: {out_path} ({os.path.getsize(out_path) // 1024} KB)")


if __name__ == "__main__":
    main()
