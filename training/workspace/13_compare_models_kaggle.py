#!/usr/bin/env python3
# Step 13 — Kaggle kernel: head-to-head of the two trained models
# Datasets: bolt-photos-v2 + bolt-points + bolt-models-cmp
#
# The two training runs cannot be compared by their own results.csv: each one
# drew its own validation split, and 29 of the 40 photos the September run held
# out were in the July model's *training* set. Scoring both on that split would
# let the July model answer from memory.
#
# So both models are scored here on the same tiles, built from the 11 photos
# that neither model trained on (July val split n September val split, plus the
# photos absent from the July dataset entirely).
import os, subprocess, sys, textwrap

VENV = "/tmp/evalenv"
VP, VPIP = f"{VENV}/bin/python", f"{VENV}/bin/pip"
print("=== SETUP ===", flush=True)
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "virtualenv"])
subprocess.check_call([sys.executable, "-m", "virtualenv", VENV])
subprocess.check_call([VPIP, "install", "-q",
                       "torch==2.2.2+cu118", "torchvision==0.17.2+cu118",
                       "--index-url", "https://download.pytorch.org/whl/cu118"])
subprocess.check_call([VPIP, "install", "-q", "numpy<2", "ultralytics", "pillow"])

SCRIPT = "/tmp/bolt_compare.py"
with open(SCRIPT, "w") as f:
    f.write(textwrap.dedent(r"""
        import os, glob, json
        import torch
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None

        TILE, OVERLAP, DEFAULT_R = 1024, 0.20, 12

        # the 11 photos neither model was trained on
        CLEAN = [
            "bila-skala-v-praze-plotynka.jpg",
            "frankenjura-hartenfels.jpg",
            "geyikbayiri-sincap.jpg",
            "his-pamatnik-krasnych-zen-2.jpeg",
            "his-prakavarna.jpeg",
            "kacov-stena-lajdaku-2.jpeg",
            "kreutzberg-levy-masiv2.jpg",
            "prokopske-udoli-borova-skala-pravy-amfiteatr3.jpg",
            "rastenfeld-linker-teil3.jpg",
            "szklarska-poreba-krzywe-baszty.jpg",
            "zboreny-kostelec-kostelecka-stena.jpg",
        ]

        def find_dir(name):
            for p in (f"/kaggle/input/datasets/pavelzbytovsk/{name}", f"/kaggle/input/{name}"):
                if os.path.isdir(p): return p
            h = glob.glob(f"/kaggle/input/**/{name}", recursive=True)
            return h[0] if h else None

        PHOTOS = find_dir("bolt-photos-v2")
        MODELS = find_dir("bolt-models-cmp")
        points = json.load(open(os.path.join(find_dir("bolt-points"), "points.json")))["photos"]
        DEVICE = "0" if torch.cuda.is_available() else "cpu"
        print(f"DEVICE={DEVICE}  PHOTOS={PHOTOS}  MODELS={MODELS}", flush=True)

        BASE = "/tmp/cmp"
        os.makedirs(f"{BASE}/images/val", exist_ok=True)
        os.makedirs(f"{BASE}/labels/val", exist_ok=True)

        def tile_pos(size):
            stride = int(TILE * (1 - OVERLAP))
            if size <= TILE: return [0]
            pos = list(range(0, size - TILE + 1, stride))
            if pos[-1] != size - TILE: pos.append(size - TILE)
            return pos

        # Every tile is kept — no random background sampling. The set has to be
        # identical for both models and reproducible.
        n_tiles = n_boxes = 0
        for fname in CLEAN:
            im = Image.open(os.path.join(PHOTOS, fname)).convert("RGB")
            W, H = im.size
            bolts = [(cx * W, cy * H, r if r else DEFAULT_R)
                     for cx, cy, r in points[fname]["bolts"]]
            stem = os.path.splitext(fname)[0]
            for tx in tile_pos(W):
                for ty in tile_pos(H):
                    tw, th = min(TILE, W - tx), min(TILE, H - ty)
                    labels = []
                    for px, py, rr in bolts:
                        if tx <= px < tx + tw and ty <= py < ty + th:
                            x1, y1 = max(tx, px-rr), max(ty, py-rr)
                            x2, y2 = min(tx+tw, px+rr), min(ty+th, py+rr)
                            bw, bh = x2-x1, y2-y1
                            if bw < 2 or bh < 2: continue
                            labels.append("0 %.6f %.6f %.6f %.6f" % (
                                ((x1+x2)/2-tx)/tw, ((y1+y2)/2-ty)/th, bw/tw, bh/th))
                    im.crop((tx, ty, tx+tw, ty+th)).save(
                        f"{BASE}/images/val/{stem}__{tx}_{ty}.jpg", "JPEG", quality=92)
                    open(f"{BASE}/labels/val/{stem}__{tx}_{ty}.txt", "w").write("\n".join(labels))
                    n_tiles += 1; n_boxes += len(labels)
            im.close()
        print(f"eval set: {len(CLEAN)} photos, {n_tiles} tiles, {n_boxes} boxes", flush=True)

        with open(f"{BASE}/data.yaml", "w") as yf:
            yf.write(f"path: {BASE}\ntrain: images/val\nval: images/val\nnc: 1\nnames: ['bolt']\n")

        from ultralytics import YOLO
        results = {}
        for tag, fn in (("v1 (2026-07-16)", "best_v1.pt"), ("v2 (2026-09-06)", "best_v2.pt")):
            path = os.path.join(MODELS, fn)
            print(f"\n=== {tag}: {path} ===", flush=True)
            m = YOLO(path).val(data=f"{BASE}/data.yaml", imgsz=TILE, batch=4,
                               device=DEVICE, split="val", verbose=False,
                               project="/kaggle/working/val", name=fn.replace(".pt",""),
                               exist_ok=True)
            b = m.box
            results[tag] = dict(mAP50=float(b.map50), mAP5095=float(b.map),
                                precision=float(b.mp), recall=float(b.mr))

        print("\n=== HEAD TO HEAD (identical tiles) ===", flush=True)
        print(f"{'model':20s} {'mAP50':>8s} {'mAP50-95':>9s} {'P':>7s} {'R':>7s}", flush=True)
        for tag, r in results.items():
            print(f"{tag:20s} {r['mAP50']:8.3f} {r['mAP5095']:9.3f} "
                  f"{r['precision']:7.3f} {r['recall']:7.3f}", flush=True)
        results["_meta"] = dict(photos=len(CLEAN), tiles=n_tiles, boxes=n_boxes)
        json.dump(results, open("/kaggle/working/comparison.json", "w"), indent=1)
        print("\nwritten /kaggle/working/comparison.json", flush=True)
    """))

print("=== Running via venv ===", flush=True)
subprocess.check_call([VP, SCRIPT])
