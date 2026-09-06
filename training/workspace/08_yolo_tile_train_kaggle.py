#!/usr/bin/env python3
# Step 08 — Kaggle kernel: YOLOv8n tiling training for bolt detection
# Datasets: bolt-photos-v2 + bolt-points
# Images are sliced into 1024px tiles at native resolution (a bolt at ~10px stays ~10px).
import os, subprocess, sys, textwrap

print("=== SETUP: isolated virtualenv ===", flush=True)
VENV = "/tmp/trainenv"
VP   = f"{VENV}/bin/python"
VPIP = f"{VENV}/bin/pip"

subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "virtualenv"])
subprocess.check_call([sys.executable, "-m", "virtualenv", VENV])
subprocess.check_call([
    VPIP, "install", "-q",
    "torch==2.2.2+cu118", "torchvision==0.17.2+cu118",
    "--index-url", "https://download.pytorch.org/whl/cu118",
])
subprocess.check_call([VPIP, "install", "-q", "numpy<2", "ultralytics", "pillow"])

TRAIN_SCRIPT = "/tmp/bolt_tile_train.py"
with open(TRAIN_SCRIPT, "w") as f:
    f.write(textwrap.dedent(r"""
        import os, glob, json, random, shutil
        import torch, numpy as np
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None

        TILE      = 1024
        OVERLAP   = 0.20
        DEFAULT_R = 12
        NEG_KEEP  = 0.15
        VAL_FRAC  = 0.10
        EPOCHS    = 100
        SEED      = 0
        random.seed(SEED)

        if torch.cuda.is_available():
            try:
                _ = (torch.zeros(2, device="cuda") + 1).sum()
                DEVICE = "0"
                print(f"GPU: {torch.cuda.get_device_name(0)}", flush=True)
            except Exception as e:
                DEVICE = "cpu"; print(f"GPU failed: {e}", flush=True)
        else:
            DEVICE = "cpu"
        print(f"DEVICE={DEVICE}", flush=True)

        def find_dir(name):
            for p in (f"/kaggle/input/datasets/pavelzbytovsk/{name}",
                      f"/kaggle/input/{name}"):
                if os.path.isdir(p): return p
            h = glob.glob(f"/kaggle/input/**/{name}", recursive=True)
            return h[0] if h else None

        PHOTOS  = find_dir("bolt-photos-v2")
        PTS_DIR = find_dir("bolt-points")
        doc     = json.load(open(os.path.join(PTS_DIR, "points.json")))
        assert doc.get("version") == 2, f"points.json version {doc.get('version')}, expected 2"
        points  = doc["photos"]
        print(f"PHOTOS={PHOTOS}  photos with points={len(points)}", flush=True)

        # Filenames in bolt-photos-v2 are slugs and so are the keys here, so a
        # plain join is enough — no guessing at how Kaggle mangled a name.
        # Anything that still fails to resolve is a broken upload, not a photo
        # to quietly drop: an earlier run trained on 85% of the data this way
        # and nothing in the log said so.
        missing = [k for k in points if not os.path.isfile(os.path.join(PHOTOS, k))]
        if missing:
            print(f"ERROR: {len(missing)} of {len(points)} photos are not in the dataset:",
                  flush=True)
            for k in missing[:40]:
                print(f"    {k}  ({points[k]['file']})", flush=True)
            raise SystemExit("re-run step 05 before training")

        files = sorted(points.keys())
        random.Random(SEED).shuffle(files)
        n_val    = max(1, int(len(files) * VAL_FRAC))
        val_set  = set(files[:n_val])

        BASE = "/tmp/bolt-tiles"
        for sub in ("images/train","images/val","labels/train","labels/val"):
            os.makedirs(os.path.join(BASE, sub), exist_ok=True)

        def tile_pos(size):
            stride = int(TILE * (1 - OVERLAP))
            if size <= TILE: return [0]
            pos = list(range(0, size - TILE + 1, stride))
            if pos[-1] != size - TILE: pos.append(size - TILE)
            return pos

        stats = {"tiles": 0, "pos": 0, "neg": 0, "hardneg": 0, "boxes": 0, "skip": 0}

        def process(fname, split):
            p = os.path.join(PHOTOS, fname)
            try: im = Image.open(p).convert("RGB")
            except Exception as e:
                print(f"  unreadable {fname}: {e}", flush=True)
                stats["skip"] += 1; return
            W, H = im.size
            rec = points[fname]
            abolts = [(cx * W, cy * H, r if r else DEFAULT_R)
                      for cx, cy, r in rec["bolts"]]
            # Crops a human opened and confirmed hold no bolt. They are the
            # background the model gets wrong — rusty stains, bolt-shaped
            # shadows, old chipped hangers — so the tiles covering them are
            # worth more than a random empty patch of rock and are always kept.
            anegs = [(cx * W, cy * H) for cx, cy, _ in rec["negatives"]]
            stem = os.path.splitext(fname)[0]
            for tx in tile_pos(W):
                for ty in tile_pos(H):
                    tw = min(TILE, W - tx); th = min(TILE, H - ty)
                    labels = []
                    for px, py, rr in abolts:
                        if tx <= px < tx + tw and ty <= py < ty + th:
                            x1 = max(tx, px-rr); y1 = max(ty, py-rr)
                            x2 = min(tx+tw, px+rr); y2 = min(ty+th, py+rr)
                            bw = x2-x1; bh = y2-y1
                            if bw < 2 or bh < 2: continue
                            ncx = ((x1+x2)/2-tx)/tw; ncy = ((y1+y2)/2-ty)/th
                            labels.append(f"0 {ncx:.6f} {ncy:.6f} {bw/tw:.6f} {bh/th:.6f}")
                    is_pos = bool(labels)
                    is_hard = not is_pos and any(
                        tx <= px < tx + tw and ty <= py < ty + th for px, py in anegs)
                    if not is_pos and not is_hard and random.random() > NEG_KEEP:
                        continue
                    im.crop((tx, ty, tx+tw, ty+th)).save(
                        os.path.join(BASE, f"images/{split}/{stem}__{tx}_{ty}.jpg"),
                        "JPEG", quality=92)
                    with open(os.path.join(BASE, f"labels/{split}/{stem}__{tx}_{ty}.txt"), "w") as lf:
                        lf.write("\n".join(labels))
                    stats["tiles"] += 1; stats["boxes"] += len(labels)
                    stats["pos" if is_pos else "neg"] += 1
                    if is_hard: stats["hardneg"] += 1
            im.close()

        print("=== TILING ===", flush=True)
        for i, fname in enumerate(files):
            process(fname, "val" if fname in val_set else "train")
            if (i+1) % 50 == 0:
                print(f"  {i+1}/{len(files)}  {stats}", flush=True)
        print(f"DONE: {stats}", flush=True)

        with open(os.path.join(BASE, "data.yaml"), "w") as yf:
            yf.write(f"path: {BASE}\ntrain: images/train\nval: images/val\n"
                     f"nc: 1\nnames: ['bolt']\n")

        from ultralytics import YOLO
        print("=== TRAINING ===", flush=True)
        model = YOLO("yolov8n.pt")
        model.train(
            data=os.path.join(BASE, "data.yaml"),
            epochs=EPOCHS, imgsz=TILE, batch=4, patience=20,
            device=DEVICE, amp=False,
            project="/kaggle/working/runs", name="bolt_tile", exist_ok=True,
            mosaic=1.0, close_mosaic=10,
            hsv_h=0.015, hsv_s=0.5, hsv_v=0.4,
            degrees=5, translate=0.1, scale=0.4, fliplr=0.5,
        )
        print("=== TRAINING COMPLETE ===", flush=True)

        best = "/kaggle/working/runs/bolt_tile/weights/best.pt"
        YOLO(best).export(format="onnx", imgsz=TILE, opset=12, simplify=True)
        for fn in ["best.pt", "best.onnx", "last.pt"]:
            src = f"/kaggle/working/runs/bolt_tile/weights/{fn}"
            if os.path.exists(src): shutil.copy(src, f"/kaggle/working/{fn}")
        for fn in os.listdir("/kaggle/working/runs/bolt_tile/"):
            if fn.endswith((".png", ".csv", ".yaml")):
                shutil.copy(f"/kaggle/working/runs/bolt_tile/{fn}", f"/kaggle/working/{fn}")
        print("=== DONE ===", flush=True)
        for fn in sorted(os.listdir("/kaggle/working/")):
            if not fn.startswith("runs"):
                sz = os.path.getsize(f"/kaggle/working/{fn}")
                print(f"  {fn}  ({sz/1e6:.1f} MB)", flush=True)
    """))

print("=== Running via venv ===", flush=True)
subprocess.check_call([VP, TRAIN_SCRIPT])
