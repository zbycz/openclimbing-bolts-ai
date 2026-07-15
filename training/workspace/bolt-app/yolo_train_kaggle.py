#!/usr/bin/env python3
# YOLOv8n trénink detekce borháků — Kaggle kernel
# Datasets: pavelzbytovsk/bolt-photos  +  pavelzbytovsk/bolt-labels
import os, subprocess, sys, shutil, textwrap

print("=== SETUP: izolovaný virtualenv ===", flush=True)

# Kaggle injectuje numpy 2.x do sys.path → venv s vlastními balíčky to obejde
# python3-venv není na Kaggle → používáme 'virtualenv' (má bundled pip)
VENV = "/tmp/trainenv"
VP   = f"{VENV}/bin/python"
VPIP = f"{VENV}/bin/pip"

print("Instaluji virtualenv...", flush=True)
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "virtualenv"])
print("Vytvářím virtualenv...", flush=True)
subprocess.check_call([sys.executable, "-m", "virtualenv", VENV])

print("Instaluji torch 2.2.2+cu118 do venv (sm_60 podpora)...", flush=True)
subprocess.check_call([
    VPIP, "install", "-q",
    "torch==2.2.2+cu118", "torchvision==0.17.2+cu118",
    "--index-url", "https://download.pytorch.org/whl/cu118",
])
print("Instaluji numpy<2 + ultralytics do venv...", flush=True)
subprocess.check_call([VPIP, "install", "-q", "numpy<2", "ultralytics"])

# ── Spusť trénink přes venv python (čistý sys.path, žádný Kaggle numpy 2.x) ──
TRAIN_SCRIPT = "/tmp/bolt_train.py"
with open(TRAIN_SCRIPT, "w") as f:
    f.write(textwrap.dedent("""
        import os, shutil
        import torch
        import numpy as np

        print(f"torch: {torch.__version__}", flush=True)
        print(f"numpy: {np.__version__}", flush=True)

        # Test numpy interop
        arr = np.zeros((3, 4, 4), dtype=np.float32)
        t = torch.from_numpy(arr)
        print(f"torch.from_numpy OK: {t.shape}", flush=True)

        # GPU check
        if torch.cuda.is_available():
            major, minor = torch.cuda.get_device_capability(0)
            name = torch.cuda.get_device_name(0)
            print(f"GPU: {name} (sm_{major}{minor})", flush=True)
            try:
                _ = (torch.zeros(2, device='cuda') + 1).sum()
                DEVICE = '0'
                print("GPU OK — trénuji na GPU", flush=True)
            except Exception as e:
                DEVICE = 'cpu'
                print(f"GPU test selhal ({e}), trénuji na CPU", flush=True)
        else:
            DEVICE = 'cpu'
            print("Žádné GPU, trénuji na CPU", flush=True)

        from ultralytics import YOLO

        PHOTOS_DIR = "/kaggle/input/datasets/pavelzbytovsk/bolt-photos"
        LABELS_DIR = "/kaggle/input/datasets/pavelzbytovsk/bolt-labels"
        BASE       = "/tmp/bolt-dataset"

        print("Stavím dataset strukturu...", flush=True)
        for sub in ("images/train", "images/val", "labels/train", "labels/val"):
            os.makedirs(os.path.join(BASE, sub), exist_ok=True)

        for sp in ("train", "val"):
            lbl_dir = os.path.join(LABELS_DIR, f"labels/{sp}")
            if not os.path.isdir(lbl_dir):
                print(f"  WARN: {lbl_dir} neexistuje", flush=True)
                continue
            for lbl_file in os.listdir(lbl_dir):
                stem = os.path.splitext(lbl_file)[0]
                shutil.copy(
                    os.path.join(lbl_dir, lbl_file),
                    os.path.join(BASE, f"labels/{sp}", lbl_file)
                )
                for ext in (".jpg", ".jpeg", ".JPG", ".JPEG"):
                    src = os.path.join(PHOTOS_DIR, stem + ext)
                    if os.path.exists(src):
                        dst = os.path.join(BASE, f"images/{sp}", stem + ext)
                        if not os.path.exists(dst):
                            os.symlink(src, dst)
                        break

        n_train = len(os.listdir(os.path.join(BASE, "images/train")))
        n_val   = len(os.listdir(os.path.join(BASE, "images/val")))
        print(f"  train: {n_train}  val: {n_val}", flush=True)

        shutil.copy(os.path.join(LABELS_DIR, "data.yaml"), os.path.join(BASE, "data.yaml"))

        print("=== TRÉNINK START ===", flush=True)
        model = YOLO("yolov8n.pt")
        model.train(
            data=os.path.join(BASE, "data.yaml"),
            epochs=100,
            imgsz=1280,
            batch=4,
            patience=20,
            device=DEVICE,
            amp=False,
            project="/kaggle/working/runs",
            name="bolt",
            exist_ok=True,
            mosaic=1.0, close_mosaic=10,
            hsv_h=0.015, hsv_s=0.5, hsv_v=0.4,
            degrees=5, translate=0.1, scale=0.4, fliplr=0.5,
            verbose=True,
        )
        print("=== TRÉNINK HOTOV ===", flush=True)

        print("Exportuji ONNX...", flush=True)
        best = "/kaggle/working/runs/bolt/weights/best.pt"
        YOLO(best).export(format="onnx", imgsz=1280, opset=12, simplify=True)

        for f in ["best.pt", "best.onnx", "last.pt"]:
            src = f"/kaggle/working/runs/bolt/weights/{f}"
            if os.path.exists(src):
                shutil.copy(src, f"/kaggle/working/{f}")
        for f in os.listdir("/kaggle/working/runs/bolt/"):
            if f.endswith((".png", ".csv", ".yaml")):
                shutil.copy(f"/kaggle/working/runs/bolt/{f}", f"/kaggle/working/{f}")

        print("=== HOTOVO ===", flush=True)
        for f in sorted(os.listdir("/kaggle/working/")):
            if not f.startswith("runs"):
                size = os.path.getsize(f"/kaggle/working/{f}")
                print(f"  {f}  ({size/1e6:.1f} MB)", flush=True)
    """))

print("=== Spouštím trénink přes venv python ===", flush=True)
subprocess.check_call([VP, TRAIN_SCRIPT])
