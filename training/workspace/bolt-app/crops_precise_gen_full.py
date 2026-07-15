#!/usr/bin/env python3
"""
Vyseká přesné výřezy (1:1 pixely) všech potvrzených boltů (type='bolt').
box = (cx±r, cy±r) v originálních pixelech. Uloží do ./crops-precise-full/.
Manifest = seznam dict {name, page, image, pos} pro navigaci zpět do /crops.
"""
import json, os, re, sqlite3
from collections import defaultdict
from PIL import Image
Image.MAX_IMAGE_PIXELS = None

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "..", "climbing_paths.sqlite")
IMG_DIR = os.path.join(HERE, "images")
OUT_DIR = os.path.join(HERE, "crops-precise-full")
PER_PAGE = 100
DEFAULT_R = 10.0

crops = json.load(open(os.path.join(HERE, "crops.json"), encoding="utf-8"))
seen = set(); BOLTS = []; img2file = {}
for c in crops:
    k = (c["image"], c["pos"])
    img2file[c["image"]] = c["file"]
    if k not in seen:
        seen.add(k); BOLTS.append(k)

# index každého klíče v BOLTS → správná stránka v /crops
bolt_page = {k: i // PER_PAGE + 1 for i, k in enumerate(BOLTS)}
bolt_idx_map = {k: i for i, k in enumerate(BOLTS)}

conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
rows = {(r["image"], r["position"]): r for r in
        conn.execute("SELECT image, position, type, cx, cy, radius_px FROM crop_labels")}
conn.close()

# všechny potvrzené bolty, v pořadí stránkování; i = index v sel (pro název souboru)
sel = [(k, rows[k]) for k in BOLTS if rows.get(k) and rows[k]["type"] == "bolt"]
print(f"boltů k vysekání: {len(sel)}")

by_img = defaultdict(list)
for i, (k, r) in enumerate(sel):
    by_img[k[0]].append((i, k, r))

os.makedirs(OUT_DIR, exist_ok=True)
for f in os.listdir(OUT_DIR):
    if f.endswith(".jpg"):
        os.remove(os.path.join(OUT_DIR, f))

def safe(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)

manifest = []   # list of {name, page, image, pos}
n_ok = n_fail = 0
for image, items in by_img.items():
    fname = img2file[image]
    path = os.path.join(IMG_DIR, fname)
    try:
        im = Image.open(path).convert("RGB"); im.load()
    except Exception:
        n_fail += len(items); continue
    W, H = im.size
    for i, k, r in items:
        cx, cy = r["cx"] * W, r["cy"] * H
        rad = r["radius_px"] or DEFAULT_R
        left = max(0, round(cx - rad)); top = max(0, round(cy - rad))
        right = min(W, round(cx + rad)); bottom = min(H, round(cy + rad))
        if right - left < 2 or bottom - top < 2:
            n_fail += 1; continue
        crop = im.crop((left, top, right, bottom))
        name = f"{i:04d}__{safe(os.path.splitext(fname)[0])}__{safe(r['position'])}.jpg"
        crop.save(os.path.join(OUT_DIR, name), "JPEG", quality=95)
        manifest.append({
            "name": name,
            "page": bolt_page[k],
            "bolt_idx": bolt_idx_map[k],
            "image": k[0],
            "pos": k[1],
        })
        n_ok += 1
    im.close()

manifest.sort(key=lambda x: x["name"])
json.dump(manifest, open(os.path.join(OUT_DIR, "_manifest.json"), "w", encoding="utf-8"),
          ensure_ascii=False)
print(f"vyseknuto: {n_ok}  selhalo: {n_fail}")
print(f"složka: {OUT_DIR}  (+ _manifest.json)")
