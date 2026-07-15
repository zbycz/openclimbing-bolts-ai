#!/usr/bin/env python3
# Fáze 3 – SAM měření poloměru borháků (Kaggle kernel, P100 GPU)
# Pro každý klik-bod vyřízne nativní okno z fotky, pustí SAM s center-point
# promptem, z masky spočítá poloměr v originálních pixelech.
# Datasets: pavelzbytovsk/bolt-photos  +  pavelzbytovsk/bolt-points
import os, subprocess, sys, textwrap

print("=== SETUP: izolovaný virtualenv (sm_60 / P100) ===", flush=True)
VENV = "/tmp/samenv"
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

SCRIPT = "/tmp/sam_radius.py"
with open(SCRIPT, "w") as f:
    f.write(textwrap.dedent("""
        import os, glob, json, math, csv
        import numpy as np
        import torch
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None

        WIN       = 160     # poloviční okno výřezu v nativních px (320x320 okno)
        R_MIN, R_MAX = 3.0, 60.0
        DEVICE = '0' if torch.cuda.is_available() else 'cpu'
        print(f"DEVICE={DEVICE}", flush=True)

        def find_dir(name):
            for pat in (f"/kaggle/input/datasets/pavelzbytovsk/{name}",
                        f"/kaggle/input/{name}"):
                if os.path.isdir(pat): return pat
            hits = glob.glob(f"/kaggle/input/**/{name}", recursive=True)
            return hits[0] if hits else None

        PHOTOS = find_dir("bolt-photos")
        PTS = json.load(open(os.path.join(find_dir("bolt-points"), "points_sam.json")))
        print(f"PHOTOS={PHOTOS}  bodů={len(PTS)}", flush=True)

        from ultralytics import SAM
        model = SAM("sam_b.pt")   # ViT-B, stáhne se automaticky

        def find_photo(fname):
            p = os.path.join(PHOTOS, fname)
            if os.path.isfile(p): return p
            stem = os.path.splitext(fname)[0]
            for ext in (".jpg",".jpeg",".JPG",".JPEG",".png"):
                p = os.path.join(PHOTOS, stem+ext)
                if os.path.isfile(p): return p
            hits = glob.glob(os.path.join(PHOTOS, "**", fname), recursive=True)
            return hits[0] if hits else None

        # seskup body podle fotky (otevři každou fotku jen jednou)
        by_file = {}
        for p in PTS:
            by_file.setdefault(p["file"], []).append(p)

        out = []
        n_done = 0; n_ok = 0; n_fail = 0; n_clamp = 0
        for fi, (fname, pts) in enumerate(by_file.items()):
            path = find_photo(fname)
            if not path:
                for p in pts: out.append({**p, "radius_px": "", "note": "no-photo"})
                n_fail += len(pts); continue
            try:
                im = Image.open(path).convert("RGB")
            except Exception as e:
                for p in pts: out.append({**p, "radius_px": "", "note": "open-fail"})
                n_fail += len(pts); continue
            W, H = im.size
            arr = np.asarray(im)
            for p in pts:
                px, py = p["cx"] * W, p["cy"] * H
                x0 = max(0, int(px - WIN)); y0 = max(0, int(py - WIN))
                x1 = min(W, int(px + WIN)); y1 = min(H, int(py + WIN))
                crop = arr[y0:y1, x0:x1]
                # bod ve výřezu
                lx, ly = px - x0, py - y0
                try:
                    res = model(crop, points=[[lx, ly]], labels=[1],
                                verbose=False, device=DEVICE)
                    m = res[0].masks
                    if m is None or len(m.data) == 0:
                        out.append({**p, "radius_px": "", "note": "no-mask"})
                        n_fail += 1; continue
                    mask = m.data[0].cpu().numpy().astype(bool)
                    area = int(mask.sum())
                    radius = math.sqrt(area / math.pi)  # ekvivalent kruhu, native px
                    note = "ok"
                    if radius < R_MIN: radius = R_MIN; note = "clamp-lo"; n_clamp += 1
                    elif radius > R_MAX: radius = R_MAX; note = "clamp-hi"; n_clamp += 1
                    else: n_ok += 1
                    out.append({**p, "radius_px": round(radius, 2), "note": note})
                except Exception as e:
                    out.append({**p, "radius_px": "", "note": f"err"})
                    n_fail += 1
                n_done += 1
            if (fi+1) % 25 == 0:
                print(f"  {fi+1}/{len(by_file)} fotek  body={n_done} "
                      f"ok={n_ok} clamp={n_clamp} fail={n_fail}", flush=True)
            im.close()

        # zápis CSV + JSON
        cols = ["file","osmId","osmType","key","position","cx","cy","radius_px","note"]
        with open("/kaggle/working/sam_radius.csv", "w", newline="") as cf:
            w = csv.DictWriter(cf, fieldnames=cols); w.writeheader()
            for r in out: w.writerow({k: r.get(k,"") for k in cols})
        with open("/kaggle/working/sam_radius.json", "w") as jf:
            json.dump(out, jf, ensure_ascii=False)

        print(f"=== HOTOVO ===  body={n_done} ok={n_ok} clamp={n_clamp} fail={n_fail}", flush=True)
        rr = [r["radius_px"] for r in out if isinstance(r["radius_px"], (int,float))]
        if rr:
            rr.sort()
            print(f"radius_px  min={rr[0]:.1f}  med={rr[len(rr)//2]:.1f}  max={rr[-1]:.1f}", flush=True)
    """))

print("=== Spouštím SAM přes venv python ===", flush=True)
subprocess.check_call([VP, SCRIPT])
