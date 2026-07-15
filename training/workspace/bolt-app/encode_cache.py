"""
Encode crop images to base64 JPEG with disk cache in ./cache_80px/.

Usage:
    from encode_cache import encode_all

    b64 = encode_all(crops, half=80)   # list[str], same order as crops
"""
import base64
import io
import os

from PIL import Image

HERE     = os.path.dirname(os.path.abspath(__file__))
IMG_DIR  = os.path.join(HERE, "images")
CACHE_DIR = os.path.join(HERE, "cache_80px")
os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(c: dict, half: int) -> str:
    safe = c["file"].replace("/", "_")
    return os.path.join(CACHE_DIR, f"{safe}__{c['x']}_{c['y']}_{half}.b64")


def _encode_one(c: dict, half: int) -> str:
    src = Image.open(os.path.join(IMG_DIR, c["file"])).convert("RGB")
    W, H = src.size
    cx, cy = c["x"] * W, c["y"] * H
    l = max(0, int(round(cx - half)))
    t = max(0, int(round(cy - half)))
    img = src.crop((l, t, min(W, l + 2 * half), min(H, t + 2 * half)))
    img = img.resize((256, 256), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def encode_all(crops: list, half: int = 80,
               report_every: int = 100) -> list[str]:
    """Return list of b64 strings, one per crop. Prints progress every report_every images."""
    total = len(crops)
    result = []
    cached = 0

    print(f"Připravuji obrázky ({total} ks, half={half}px, cache: cache_80px/)...", flush=True)

    for i, c in enumerate(crops):
        path = _cache_path(c, half)
        if os.path.exists(path):
            with open(path) as f:
                b64 = f.read()
            cached += 1
        else:
            b64 = _encode_one(c, half)
            with open(path, "w") as f:
                f.write(b64)
        result.append(b64)

        done = i + 1
        if done % report_every == 0 or done == total:
            new = done - cached if cached <= done else 0
            print(f"  [{done:>5}/{total}]  z cache: {cached}  nově enkódováno: {done - cached}", flush=True)

    print(f"Hotovo. Z cache: {cached}/{total}, nově uloženo: {total - cached}/{total}\n", flush=True)
    return result
