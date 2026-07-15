#!/usr/bin/env python3
"""
Trénovací dataset = VŠECHNY zrevidované bolty (type='bolt') z celého datasetu.
undecided i no-bolt se IGNORUJÍ – síť se přes ně nemá učit.

Výstup: bolt-points/points_precise.json = { "file.jpg": [[cx,cy,radius_px], ...] }
"""
import json, os, sqlite3
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "..", "climbing_paths.sqlite")
OUT = os.path.join(HERE, "bolt-points", "points_precise.json")
DEFAULT_R = 10.0

crops = json.load(open(os.path.join(HERE, "crops.json"), encoding="utf-8"))
img2file = {}
for c in crops:
    img2file[c["image"]] = c["file"]

conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
rows = [r for r in conn.execute(
    "SELECT image, position, cx, cy, radius_px FROM crop_labels WHERE type='bolt'")]
conn.close()

out = defaultdict(list)
for r in rows:
    f = img2file.get(r["image"])
    if not f:
        continue
    out[f].append([r["cx"], r["cy"], (r["radius_px"] or DEFAULT_R)])

out = dict(out)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
n = sum(len(v) for v in out.values())
print(f"pozitiv (bolt): {n}")
print(f"obrázků: {len(out)}")
print(f"zapsáno: {OUT}  ({os.path.getsize(OUT)/1024:.0f} KB)")
