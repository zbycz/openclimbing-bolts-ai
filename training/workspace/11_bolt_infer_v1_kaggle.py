#!/usr/bin/env python3
# Step 11 — Kaggle kernel: ONNX inference on all photos
# Datasets: bolt-photos + bolt-model-v1
# Output: detections_v1.sqlite (normalized bbox coordinates)
import os, glob, sqlite3, time
import numpy as np
from PIL import Image
Image.MAX_IMAGE_PIXELS = None

def find_dir(name):
    for p in (f"/kaggle/input/datasets/pavelzbytovsk/{name}", f"/kaggle/input/{name}"):
        if os.path.isdir(p): return p
    h = glob.glob(f"/kaggle/input/**/{name}", recursive=True)
    return h[0] if h else None

PHOTOS_DIR = find_dir("bolt-photos")
MODEL_DIR  = find_dir("bolt-model-v1")
print(f"PHOTOS={PHOTOS_DIR}", flush=True)
print(f"MODEL={MODEL_DIR}", flush=True)

# Find the ONNX file (best_v1.onnx or best.onnx)
MODEL_PATH = None
for candidate in ("best_v1.onnx", "best.onnx"):
    p = os.path.join(MODEL_DIR, candidate)
    if os.path.isfile(p):
        MODEL_PATH = p
        break
if MODEL_PATH is None:
    raise FileNotFoundError(f"ONNX model not found in {MODEL_DIR}")

import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "onnxruntime"])
import onnxruntime as ort
sess     = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
IN_NAME  = sess.get_inputs()[0].name
OUT_NAME = sess.get_outputs()[0].name
print(f"Model: {MODEL_PATH}", flush=True)

TILE    = 1024
OVERLAP = 0.20
STRIDE  = round(TILE * (1 - OVERLAP))
CONF    = 0.25
NMS_IOU = 0.45

def tile_pos(size):
    if size <= TILE: return [0]
    pos = list(range(0, size - TILE + 1, STRIDE))
    if pos[-1] != size - TILE: pos.append(size - TILE)
    return pos

def iou(a, b):
    ax1,ay1 = a[0]-a[2]/2, a[1]-a[3]/2
    ax2,ay2 = a[0]+a[2]/2, a[1]+a[3]/2
    bx1,by1 = b[0]-b[2]/2, b[1]-b[3]/2
    bx2,by2 = b[0]+b[2]/2, b[1]+b[3]/2
    ix = max(0, min(ax2,bx2) - max(ax1,bx1))
    iy = max(0, min(ay2,by2) - max(ay1,by1))
    inter = ix*iy
    union = a[2]*a[3] + b[2]*b[3] - inter
    return inter/union if union > 0 else 0

def nms(boxes):
    if not boxes: return []
    boxes = sorted(boxes, key=lambda x: -x[4])
    keep, used = [], [False]*len(boxes)
    for i in range(len(boxes)):
        if used[i]: continue
        keep.append(boxes[i])
        for j in range(i+1, len(boxes)):
            if not used[j] and iou(boxes[i], boxes[j]) > NMS_IOU:
                used[j] = True
    return keep

def infer_image(path):
    img = Image.open(path).convert("RGB")
    W, H = img.size
    raw = []
    for ty in tile_pos(H):
        for tx in tile_pos(W):
            tw = min(TILE, W-tx); th = min(TILE, H-ty)
            tile = Image.new("RGB", (TILE, TILE), (128, 128, 128))
            tile.paste(img.crop((tx, ty, tx+tw, ty+th)), (0, 0))
            arr    = np.array(tile, dtype=np.float32) / 255.0
            tensor = arr.transpose(2, 0, 1)[None]
            out    = sess.run([OUT_NAME], {IN_NAME: tensor})[0]  # [1,5,N]
            data   = out[0]  # [5, N]
            for i in range(data.shape[1]):
                score = float(data[4, i])
                if score < CONF: continue
                cx_t, cy_t = data[0, i], data[1, i]
                if cx_t > tw or cy_t > th: continue
                raw.append(((tx + cx_t)/W, (ty + cy_t)/H,
                             data[2,i]/W, data[3,i]/H, score))
    img.close()
    return nms(raw), W, H

OUT_DB = "/kaggle/working/detections_v1.sqlite"
conn = sqlite3.connect(OUT_DB)
conn.execute("""CREATE TABLE detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image TEXT NOT NULL, cx REAL NOT NULL, cy REAL NOT NULL,
    w REAL NOT NULL, h REAL NOT NULL, score REAL NOT NULL,
    img_w INTEGER NOT NULL, img_h INTEGER NOT NULL
)""")
conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
for k, v in [("model","v1"),("conf_thresh",str(CONF)),("nms_iou",str(NMS_IOU)),
             ("tile",str(TILE)),("overlap",str(OVERLAP))]:
    conn.execute("INSERT INTO meta VALUES (?,?)", (k, v))
conn.commit()

exts = {".jpg",".jpeg",".png",".JPG",".JPEG",".PNG"}
all_imgs = sorted(f for f in os.listdir(PHOTOS_DIR) if os.path.splitext(f)[1] in exts)
print(f"Total images: {len(all_imgs)}", flush=True)

t0 = time.time()
total_det = 0
for i, fname in enumerate(all_imgs):
    try:
        boxes, W, H = infer_image(os.path.join(PHOTOS_DIR, fname))
    except Exception as e:
        print(f"  ERROR {fname}: {e}", flush=True); continue
    conn.executemany(
        "INSERT INTO detections (image,cx,cy,w,h,score,img_w,img_h) VALUES (?,?,?,?,?,?,?,?)",
        [(fname, b[0], b[1], b[2], b[3], b[4], W, H) for b in boxes]
    )
    conn.commit()
    total_det += len(boxes)
    if (i+1) % 20 == 0 or i == len(all_imgs)-1:
        print(f"  [{i+1}/{len(all_imgs)}] {fname} → {len(boxes)} det  "
              f"(total {total_det}, {time.time()-t0:.0f}s)", flush=True)

conn.execute("INSERT INTO meta VALUES ('total_images',?)", (str(len(all_imgs)),))
conn.execute("INSERT INTO meta VALUES ('total_detections',?)", (str(total_det),))
conn.commit(); conn.close()

print(f"\nDone: {total_det} detections from {len(all_imgs)} images → {OUT_DB}", flush=True)
