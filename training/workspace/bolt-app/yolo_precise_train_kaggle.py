#!/usr/bin/env python3
# YOLOv8n PŘESNÝ trénink – jen plně zrevidované obrázky + přesné boxy z revize.
# Tiling 1024px v nativním rozlišení, boxy z ručně/SAM ověřených cx,cy,radius.
# Datasets: pavelzbytovsk/bolt-photos + pavelzbytovsk/bolt-points (points_precise.json)
import os, subprocess, sys, textwrap

print("=== SETUP: izolovaný virtualenv (sm_60 / P100) ===", flush=True)
VENV="/tmp/trainenv"; VP=f"{VENV}/bin/python"; VPIP=f"{VENV}/bin/pip"
subprocess.check_call([sys.executable,"-m","pip","install","-q","virtualenv"])
subprocess.check_call([sys.executable,"-m","virtualenv",VENV])
subprocess.check_call([VPIP,"install","-q","torch==2.2.2+cu118","torchvision==0.17.2+cu118",
                       "--index-url","https://download.pytorch.org/whl/cu118"])
subprocess.check_call([VPIP,"install","-q","numpy<2","ultralytics","pillow"])

SCRIPT="/tmp/bolt_precise_train.py"
with open(SCRIPT,"w") as f:
    f.write(textwrap.dedent("""
        import os, glob, json, random, shutil
        import torch, numpy as np
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None

        TILE=1024; OVERLAP=0.20; NEG_KEEP=0.15; VAL_FRAC=0.12; EPOCHS=120; SEED=0
        random.seed(SEED)

        DEVICE='0' if torch.cuda.is_available() else 'cpu'
        if DEVICE=='0':
            try: _=(torch.zeros(2,device='cuda')+1).sum()
            except Exception: DEVICE='cpu'
        print(f"DEVICE={DEVICE}", flush=True)

        def find_dir(name):
            for p in (f"/kaggle/input/datasets/pavelzbytovsk/{name}", f"/kaggle/input/{name}"):
                if os.path.isdir(p): return p
            h=glob.glob(f"/kaggle/input/**/{name}",recursive=True); return h[0] if h else None
        PHOTOS=find_dir("bolt-photos"); PTS=find_dir("bolt-points")
        points=json.load(open(os.path.join(PTS,"points_precise.json")))
        print(f"PHOTOS={PHOTOS}  obrázků={len(points)}  boltů={sum(len(v) for v in points.values())}", flush=True)

        files=sorted(points.keys()); random.Random(SEED).shuffle(files)
        n_val=max(1,int(len(files)*VAL_FRAC)); val_set=set(files[:n_val]);
        print(f"train obrázků={len(files)-n_val}  val obrázků={n_val}", flush=True)

        BASE="/tmp/bolt-precise"
        for s in ("images/train","images/val","labels/train","labels/val"):
            os.makedirs(os.path.join(BASE,s),exist_ok=True)

        def find_photo(fn):
            p=os.path.join(PHOTOS,fn)
            if os.path.isfile(p): return p
            st=os.path.splitext(fn)[0]
            for e in (".jpg",".jpeg",".JPG",".JPEG",".png"):
                p=os.path.join(PHOTOS,st+e)
                if os.path.isfile(p): return p
            h=glob.glob(os.path.join(PHOTOS,"**",fn),recursive=True); return h[0] if h else None

        def tpos(size,tile,stride):
            if size<=tile: return [0]
            p=list(range(0,size-tile+1,stride))
            if p[-1]!=size-tile: p.append(size-tile)
            return p

        stride=int(TILE*(1-OVERLAP))
        stats={"tiles":0,"pos":0,"neg":0,"boxes":0,"skip":0}
        def process(fn,split):
            p=find_photo(fn)
            if not p: stats["skip"]+=1; return
            try: im=Image.open(p).convert("RGB")
            except Exception: stats["skip"]+=1; return
            W,H=im.size
            ab=[(cx*W,cy*H,r) for cx,cy,r in points[fn]]
            stem=os.path.splitext(os.path.basename(p))[0].replace(" ","_")
            for tx in tpos(W,TILE,stride):
                for ty in tpos(H,TILE,stride):
                    tw=min(TILE,W-tx); th=min(TILE,H-ty); labels=[]
                    for px,py,rr in ab:
                        if tx<=px<tx+tw and ty<=py<ty+th:
                            x1=max(tx,px-rr); y1=max(ty,py-rr); x2=min(tx+tw,px+rr); y2=min(ty+th,py+rr)
                            bw=x2-x1; bh=y2-y1
                            if bw<2 or bh<2: continue
                            labels.append(f"0 {((x1+x2)/2-tx)/tw:.6f} {((y1+y2)/2-ty)/th:.6f} {bw/tw:.6f} {bh/th:.6f}")
                    ispos=bool(labels)
                    if not ispos and random.random()>NEG_KEEP: continue
                    base=f"{stem}__{tx}_{ty}"
                    im.crop((tx,ty,tx+tw,ty+th)).save(os.path.join(BASE,f"images/{split}",base+".jpg"),"JPEG",quality=92)
                    open(os.path.join(BASE,f"labels/{split}",base+".txt"),"w").write("\\n".join(labels))
                    stats["tiles"]+=1; stats["boxes"]+=len(labels); stats["pos" if ispos else "neg"]+=1
            im.close()

        print("=== ŘEŽU DLAŽDICE ===", flush=True)
        for i,fn in enumerate(files):
            process(fn,"val" if fn in val_set else "train")
            if (i+1)%50==0: print(f"  {i+1}/{len(files)}  {stats}", flush=True)
        print(f"HOTOVO dlaždice: {stats}", flush=True)

        open(os.path.join(BASE,"data.yaml"),"w").write(
            f"path: {BASE}\\ntrain: images/train\\nval: images/val\\nnc: 1\\nnames: ['bolt']\\n")

        from ultralytics import YOLO
        print("=== TRÉNINK START (imgsz=1024, přesné boxy) ===", flush=True)
        m=YOLO("yolov8n.pt")
        m.train(data=os.path.join(BASE,"data.yaml"), epochs=EPOCHS, imgsz=TILE, batch=4,
                patience=25, device=DEVICE, amp=False,
                project="/kaggle/working/runs", name="bolt_precise", exist_ok=True,
                mosaic=1.0, close_mosaic=10, hsv_h=0.015, hsv_s=0.5, hsv_v=0.4,
                degrees=5, translate=0.1, scale=0.4, fliplr=0.5, verbose=True)
        print("=== TRÉNINK HOTOV ===", flush=True)
        best="/kaggle/working/runs/bolt_precise/weights/best.pt"
        YOLO(best).export(format="onnx", imgsz=TILE, opset=12, simplify=True)
        for fn in ["best.pt","best.onnx","last.pt"]:
            s=f"/kaggle/working/runs/bolt_precise/weights/{fn}"
            if os.path.exists(s): shutil.copy(s,f"/kaggle/working/{fn}")
        for fn in os.listdir("/kaggle/working/runs/bolt_precise/"):
            if fn.endswith((".png",".csv",".yaml")): shutil.copy(f"/kaggle/working/runs/bolt_precise/{fn}", f"/kaggle/working/{fn}")
        print("=== HOTOVO ===", flush=True)
    """))

print("=== Spouštím přesný trénink přes venv python ===", flush=True)
subprocess.check_call([VP,SCRIPT])
