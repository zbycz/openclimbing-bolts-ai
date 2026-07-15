#!/usr/bin/env python3
# YOLOv8n tiling trénink detekce borháků — Kaggle kernel (Fáze 2)
# Obrázky se NEzmenšují: řežou se na dlaždice 1024px v nativním rozlišení,
# takže ~10px borhák zůstane ~10px (místo ~3px po downscale na 1280).
# Datasets: pavelzbytovsk/bolt-photos  +  pavelzbytovsk/bolt-points
import os, subprocess, sys, textwrap

print("=== SETUP: izolovaný virtualenv (sm_60 / P100) ===", flush=True)
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
    f.write(textwrap.dedent("""
        import os, glob, json, random, shutil
        import torch
        import numpy as np
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None

        # ── konfigurace ─────────────────────────────────────────────────────
        TILE        = 1024        # velikost dlaždice v nativních pixelech
        OVERLAP     = 0.20        # překryv sousedních dlaždic
        DEFAULT_R   = 12          # default poloměr boltu v px (než dojede SAM)
        NEG_KEEP    = 0.15        # podíl prázdných dlaždic ponechaných jako negativy
        VAL_FRAC    = 0.10        # podíl fotek do validace
        EPOCHS      = 100
        SEED        = 0
        random.seed(SEED)

        # ── GPU check ───────────────────────────────────────────────────────
        if torch.cuda.is_available():
            major, _ = torch.cuda.get_device_capability(0)
            print(f"GPU: {torch.cuda.get_device_name(0)} (sm_{major}x)", flush=True)
            try:
                _ = (torch.zeros(2, device='cuda') + 1).sum(); DEVICE = '0'
            except Exception as e:
                DEVICE = 'cpu'; print(f"GPU test selhal: {e}", flush=True)
        else:
            DEVICE = 'cpu'
        print(f"DEVICE={DEVICE}", flush=True)

        # ── najdi photos dir + points.json ──────────────────────────────────
        def find_dir(name):
            for pat in (f"/kaggle/input/datasets/pavelzbytovsk/{name}",
                        f"/kaggle/input/{name}"):
                if os.path.isdir(pat):
                    return pat
            hits = glob.glob(f"/kaggle/input/**/{name}", recursive=True)
            return hits[0] if hits else None

        PHOTOS = find_dir("bolt-photos")
        PTS_DIR = find_dir("bolt-points")
        print(f"PHOTOS={PHOTOS}", flush=True)
        print(f"PTS_DIR={PTS_DIR}", flush=True)
        pts_file = os.path.join(PTS_DIR, "points.json")
        points = json.load(open(pts_file, encoding="utf-8"))
        print(f"fotek s body: {len(points)}", flush=True)

        # ── rozdělení train/val po fotkách (deterministicky) ────────────────
        files = sorted(points.keys())
        random.Random(SEED).shuffle(files)
        n_val = max(1, int(len(files) * VAL_FRAC))
        val_set = set(files[:n_val]); train_files = files[n_val:]
        print(f"train fotek: {len(train_files)}  val fotek: {len(val_set)}", flush=True)

        BASE = "/tmp/bolt-tiles"
        for sub in ("images/train","images/val","labels/train","labels/val"):
            os.makedirs(os.path.join(BASE, sub), exist_ok=True)

        def find_photo(fname):
            p = os.path.join(PHOTOS, fname)
            if os.path.isfile(p): return p
            stem = os.path.splitext(fname)[0]
            for ext in (".jpg",".jpeg",".JPG",".JPEG",".png"):
                p = os.path.join(PHOTOS, stem+ext)
                if os.path.isfile(p): return p
            hits = glob.glob(os.path.join(PHOTOS, "**", fname), recursive=True)
            return hits[0] if hits else None

        def tile_positions(size, tile, stride):
            if size <= tile: return [0]
            pos = list(range(0, size - tile + 1, stride))
            if pos[-1] != size - tile: pos.append(size - tile)
            return pos

        stride = int(TILE * (1 - OVERLAP))
        stats = {"tiles":0,"pos_tiles":0,"neg_tiles":0,"boxes":0,"skipped":0}

        def process(fname, split):
            p = find_photo(fname)
            if not p:
                stats["skipped"] += 1; return
            try:
                im = Image.open(p).convert("RGB")
            except Exception as e:
                stats["skipped"] += 1; return
            W, H = im.size
            bolts = points[fname]  # [[cx,cy,radius_px], ...] normalizované
            # absolutní pixely
            abolts = []
            for cx, cy, r in bolts:
                px, py = cx * W, cy * H
                rr = (r if r else DEFAULT_R)
                abolts.append((px, py, rr))

            xs = tile_positions(W, TILE, stride)
            ys = tile_positions(H, TILE, stride)
            stem = os.path.splitext(os.path.basename(p))[0].replace(" ", "_")

            for tx in xs:
                for ty in ys:
                    tw = min(TILE, W - tx); th = min(TILE, H - ty)
                    labels = []
                    for px, py, rr in abolts:
                        if tx <= px < tx + tw and ty <= py < ty + th:
                            # box v rámci dlaždice, klip do hran
                            x1 = max(tx, px - rr); y1 = max(ty, py - rr)
                            x2 = min(tx + tw, px + rr); y2 = min(ty + th, py + rr)
                            bw = x2 - x1; bh = y2 - y1
                            if bw < 2 or bh < 2: continue
                            ncx = ((x1 + x2) / 2 - tx) / tw
                            ncy = ((y1 + y2) / 2 - ty) / th
                            nbw = bw / tw; nbh = bh / th
                            labels.append(f"0 {ncx:.6f} {ncy:.6f} {nbw:.6f} {nbh:.6f}")
                    is_pos = bool(labels)
                    if not is_pos and random.random() > NEG_KEEP:
                        continue
                    tile_img = im.crop((tx, ty, tx + tw, ty + th))
                    base = f"{stem}__{tx}_{ty}"
                    tile_img.save(os.path.join(BASE, f"images/{split}", base + ".jpg"),
                                  "JPEG", quality=92)
                    with open(os.path.join(BASE, f"labels/{split}", base + ".txt"), "w") as lf:
                        lf.write("\\n".join(labels))
                    stats["tiles"] += 1
                    stats["boxes"] += len(labels)
                    stats["pos_tiles" if is_pos else "neg_tiles"] += 1
            im.close()

        print("=== ŘEŽU DLAŽDICE ===", flush=True)
        for i, fname in enumerate(files):
            process(fname, "val" if fname in val_set else "train")
            if (i+1) % 50 == 0:
                print(f"  {i+1}/{len(files)} fotek  {stats}", flush=True)
        print(f"HOTOVO dlaždice: {stats}", flush=True)

        # ── data.yaml ───────────────────────────────────────────────────────
        with open(os.path.join(BASE, "data.yaml"), "w") as yf:
            yf.write(f"path: {BASE}\\ntrain: images/train\\nval: images/val\\n"
                     f"nc: 1\\nnames: ['bolt']\\n")

        from ultralytics import YOLO
        print("=== TRÉNINK START (imgsz=%d) ===" % TILE, flush=True)
        model = YOLO("yolov8n.pt")
        model.train(
            data=os.path.join(BASE, "data.yaml"),
            epochs=EPOCHS, imgsz=TILE, batch=4, patience=20,
            device=DEVICE, amp=False,
            project="/kaggle/working/runs", name="bolt_tile", exist_ok=True,
            mosaic=1.0, close_mosaic=10,
            hsv_h=0.015, hsv_s=0.5, hsv_v=0.4,
            degrees=5, translate=0.1, scale=0.4, fliplr=0.5, verbose=True,
        )
        print("=== TRÉNINK HOTOV ===", flush=True)

        best = "/kaggle/working/runs/bolt_tile/weights/best.pt"
        YOLO(best).export(format="onnx", imgsz=TILE, opset=12, simplify=True)
        for fn in ["best.pt","best.onnx","last.pt"]:
            src = f"/kaggle/working/runs/bolt_tile/weights/{fn}"
            if os.path.exists(src): shutil.copy(src, f"/kaggle/working/{fn}")
        for fn in os.listdir("/kaggle/working/runs/bolt_tile/"):
            if fn.endswith((".png",".csv",".yaml")):
                shutil.copy(f"/kaggle/working/runs/bolt_tile/{fn}", f"/kaggle/working/{fn}")
        print("=== HOTOVO ===", flush=True)
        for fn in sorted(os.listdir("/kaggle/working/")):
            if not fn.startswith("runs"):
                print(f"  {fn}  ({os.path.getsize(f'/kaggle/working/{fn}')/1e6:.1f} MB)", flush=True)
    """))

print("=== Spouštím tiling trénink přes venv python ===", flush=True)
subprocess.check_call([VP, TRAIN_SCRIPT])
