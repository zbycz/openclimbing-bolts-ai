#!/usr/bin/env python3
"""
Vygeneruje YOLO labely z crops.json (filtry: no_bolt DB + P4-shiny).
Fotky se NEkopírují — jsou už na Kaggle jako bolt-photos.

Výstup:
    bolt_labels/
      labels/train/*.txt
      labels/val/*.txt
      data.yaml              ← cesty pro Kaggle notebook
      split.json             ← {train:[...], val:[...]} pro notebook
    bolt_labels.zip          ← uploadujeme na Kaggle jako bolt-labels
"""
import json, os, random, shutil, sqlite3, zipfile
from PIL import Image

random.seed(42)

HERE    = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(HERE, "images")
CROPS   = json.load(open(os.path.join(HERE, "crops.json"), encoding="utf-8"))
DB_PATH = os.path.join(HERE, "..", "climbing_paths.sqlite")
SHINY   = os.path.join(HERE, "test_dataset_results_p4shiny_all.txt")
OUT     = os.path.join(HERE, "bolt_labels")

BOX_PX   = 60     # pevných 60 px v originále → per-foto normalizace na W/H fotky
VAL_FRAC = 0.10


def crop_key(c):
    return c["file"].replace(" ", "_") + "__" + c["pos"].rstrip("B").replace(",", "_") + ".jpg"


# ── filtry ────────────────────────────────────────────────────────────────────
conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
no_bolt = {
    r["image"].replace("File:", "").replace(" ", "_") + "__" +
    r["position"].rstrip("B").replace(",", "_") + ".jpg"
    for r in conn.execute("SELECT image, position FROM crops_with_no_bolts")
}
conn.close()

shiny = {}
for line in open(SHINY, encoding="utf-8"):
    line = line.strip()
    if line:
        o = json.loads(line)
        shiny[crop_key(o)] = o["result"]

def is_bolt(c):
    return crop_key(c) not in no_bolt

# ── seskup body podle fotky ───────────────────────────────────────────────────
by_photo = {}
for c in CROPS:
    by_photo.setdefault(c["file"], [])
    if is_bolt(c):
        by_photo[c["file"]].append((c["x"], c["y"]))

photos = sorted(by_photo)
random.shuffle(photos)
n_val = max(1, round(len(photos) * VAL_FRAC))
val   = set(photos[:n_val])

# ── výpočet rozměrů fotek (pro per-foto box) ─────────────────────────────────
print("Načítám rozměry fotek pro per-foto box...")
dims = {}
for i, fname in enumerate(photos):
    src = os.path.join(IMG_DIR, fname)
    if os.path.exists(src):
        dims[fname] = Image.open(src).size  # (W, H)
    if (i + 1) % 50 == 0:
        print(f"  {i+1}/{len(photos)}")
print(f"  hotovo ({len(dims)} fotek)")

# ── postav adresáře ───────────────────────────────────────────────────────────
if os.path.exists(OUT):
    shutil.rmtree(OUT)
for sub in ("labels/train", "labels/val"):
    os.makedirs(os.path.join(OUT, sub), exist_ok=True)

n_bolts = n_bg = 0
split_info = {"train": [], "val": []}

for fname in photos:
    src = os.path.join(IMG_DIR, fname)
    if not os.path.exists(src):
        continue
    safe  = fname.replace(" ", "_")
    split = "val" if fname in val else "train"
    split_info[split].append(safe)

    W, H  = dims.get(fname, (4032, 3024))
    bw, bh = BOX_PX / W, BOX_PX / H

    pts = by_photo[fname]
    lbl = os.path.join(OUT, f"labels/{split}", os.path.splitext(safe)[0] + ".txt")
    with open(lbl, "w") as f:
        for x, y in pts:
            f.write(f"0 {x:.6f} {y:.6f} {bw:.6f} {bh:.6f}\n")
    n_bolts += len(pts)
    if not pts:
        n_bg += 1

# ── data.yaml (cesty pro Kaggle notebook) ────────────────────────────────────
# Notebook rozbalí fotky do /tmp/bolt-dataset/ a zkopíruje labely
with open(os.path.join(OUT, "data.yaml"), "w") as f:
    f.write(
        "path: /tmp/bolt-dataset\n"
        "train: images/train\n"
        "val: images/val\n"
        "nc: 1\n"
        "names:\n  0: bolt\n"
    )

# ── split.json pro notebook ───────────────────────────────────────────────────
with open(os.path.join(OUT, "split.json"), "w") as f:
    json.dump(split_info, f)

# ── zip (jen labely + yaml + split, žádné fotky) ─────────────────────────────
zip_path = os.path.join(HERE, "bolt_labels.zip")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(OUT):
        for fn in files:
            full = os.path.join(root, fn)
            z.write(full, os.path.relpath(full, OUT))

kb = os.path.getsize(zip_path) / 1024
train_n = len(split_info["train"])
val_n   = len(split_info["val"])
print(f"\nFotek:     {len(photos)}  (train {train_n} / val {val_n})")
print(f"Borháků:   {n_bolts}  (bg fotek bez boltu: {n_bg})")
print(f"Box:       {BOX_PX}px per-foto (normalizováno na W/H každé fotky)")
print(f"Zip:       bolt_labels.zip  ({kb:.0f} KB)")
