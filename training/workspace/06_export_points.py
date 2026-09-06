#!/usr/bin/env python3
"""
Step 06: Export annotations from crop_labels to points.json for the Kaggle kernels.

Output: data/bolt-points/points.json

  {"version": 2,
   "photos": {"<slug>.jpg": {"file": "<original Commons title>",
                             "bolts":     [[cx, cy, radius_px], ...],
                             "negatives": [[cx, cy, radius_px], ...]}}}

  - keys are slugs (see slugify.py), matching the filenames in the
    bolt-photos-v2 dataset. Kaggle rewrites non-ASCII filenames on upload, so
    original titles cannot be used to look a photo up on the other side.
  - a photo is included if it has at least one reviewed crop (type='bolt' or
    'no-bolt'). Photos where every crop is still 'undecided' are skipped: we
    can't yet say anything about them in either direction.
  - 'bolts' are the positives. 'negatives' are crops a human looked at and
    confirmed hold no bolt — the kernel keeps every tile covering one, instead
    of leaving it to the random background sample.
  - 'undecided' crops inside an otherwise reviewed photo are left out
    entirely rather than asserted as ground truth either way.
  - radius_px may be null (the kernel falls back to 12px)

Usage:
  python3 06_export_points.py
  python3 06_export_points.py --db data/climbing_paths.sqlite --out data/bolt-points
"""
import argparse
import json
import os
import sqlite3
from collections import defaultdict

from slugify import slug_map


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

    reviewed = {img: rs for img, rs in by_image.items()
                if any(r["type"] in ("bolt", "no-bolt") for r in rs)}
    n_skipped_untouched = len(by_image) - len(reviewed)

    # slug_map over the whole set at once, so a collision is caught here rather
    # than after 2.5 GB has gone up to Kaggle under a name that overwrote another.
    slugs = slug_map({img.removeprefix("File:") for img in reviewed})

    photos = {}
    for img, rs in reviewed.items():
        fname = img.removeprefix("File:")
        photos[slugs[fname]] = {
            "file": fname,
            "bolts": [[r["cx"], r["cy"], r["radius_px"]]
                      for r in rs if r["type"] == "bolt"],
            "negatives": [[r["cx"], r["cy"], r["radius_px"]]
                          for r in rs if r["type"] == "no-bolt"],
        }

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "points.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"version": 2, "photos": photos}, f, ensure_ascii=False)

    n_bolts = sum(len(p["bolts"]) for p in photos.values())
    n_neg = sum(len(p["negatives"]) for p in photos.values())
    n_with_r = sum(1 for p in photos.values() for b in p["bolts"] if b[2] is not None)
    n_neg_only = sum(1 for p in photos.values() if not p["bolts"])
    n_undecided = sum(1 for rs in reviewed.values()
                      for r in rs if r["type"] not in ("bolt", "no-bolt"))
    print(f"Photos: {len(photos)} ({n_neg_only} with no bolt at all, "
          f"{n_skipped_untouched} skipped for being completely unreviewed)")
    print(f"Bolts: {n_bolts} ({n_with_r} with radius_px, {n_bolts - n_with_r} without)")
    print(f"Confirmed negatives: {n_neg}")
    print(f"Undecided crops inside reviewed photos, left out: {n_undecided}")
    print(f"Written: {out_path} ({os.path.getsize(out_path) // 1024} KB)")


if __name__ == "__main__":
    main()
