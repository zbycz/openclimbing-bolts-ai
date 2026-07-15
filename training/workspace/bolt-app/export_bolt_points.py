#!/usr/bin/env python3
"""
Fáze 2 příprava: export has-bolt středů (z crop_labels) pro tiling kernel.

Výstup: points.json  =  { "ALIKOU_1.jpg": [[cx, cy, radius_px], ...], ... }
  - jen type='has-bolt' (no-bolt a undecided vynecháváme — to jsou false positives)
  - radius_px může být null (Fáze 2 použije default; Fáze 4 doplní ze SAMu)
"""
import json
import os
import sqlite3
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "..", "climbing_paths.sqlite")
CROPS_JSON = os.path.join(HERE, "crops.json")
OUT_DIR = os.path.join(HERE, "bolt-points")
OUT = os.path.join(OUT_DIR, "points.json")


def main():
    # mapování (osm_id, key, pos) → file (z crops.json, kde je i 'file')
    crops = json.load(open(CROPS_JSON, encoding="utf-8"))
    key2file = {(c["osmId"], c["key"], c["pos"]): c["file"] for c in crops}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT osm_id, key, position, cx, cy, radius_px "
        "FROM crop_labels WHERE type='has-bolt'"
    ).fetchall()
    conn.close()

    points = defaultdict(list)
    missing = 0
    for r in rows:
        f = key2file.get((r["osm_id"], r["key"], r["position"]))
        if not f:
            missing += 1
            continue
        points[f].append([r["cx"], r["cy"], r["radius_px"]])

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(points, fh, ensure_ascii=False)

    n_bolts = sum(len(v) for v in points.values())
    with_r = sum(1 for v in points.values() for p in v if p[2] is not None)
    print(f"fotek: {len(points)}  has-bolt bodů: {n_bolts}")
    print(f"  s radius_px: {with_r}  bez (null→default): {n_bolts - with_r}")
    if missing:
        print(f"  POZN: {missing} bodů bez file v crops.json (přeskočeno)")
    print(f"zapsáno: {OUT}  ({os.path.getsize(OUT)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
