#!/usr/bin/env python3
"""
Bolt Viewer server (port 8001).

Routy:
  /                       statický prohlížeč (index.html)
  /crops?page=N           server-renderovaná mřížka 100 výřezů + stránkování
  /crop-img?file=&x=&y=   JPEG výřezu generovaný Pillow (±20 px), disk-cache
  /api/mark   POST        toggle type has-bolt⇄no-bolt výřezu v crop_labels
  /data.json /crops.json /images/*   statické soubory
"""

import html
import io
import json
import math
import os
import sqlite3
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingTCPServer

from PIL import Image

PORT = int(os.environ.get("BOLT_PORT", "8001"))
HERE = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.environ.get("BOLT_IMG_DIR", os.path.join(HERE, "images"))
CACHE_DIR = os.path.join(HERE, "cache")
DB_PATH = os.environ.get("BOLT_DB", os.path.join(HERE, "..", "climbing_paths.sqlite"))
CROPS_JSON = os.path.join(HERE, "crops.json")
TEST_DATASET_FILE = os.path.join(HERE, "test_dataset.txt")
TEST_RESULTS_FILE = "/tmp/or_paid_results.json"

MODELS_DIR = os.path.join(HERE, "models")
PRECISE_DIR = os.path.join(HERE, "crops-precise-571")
PRECISE_FULL_DIR = os.path.join(HERE, "crops-precise-full")

HALF = 20            # ±20 px od pozice boltu (původní rozlišení)
OUT = 200            # výsledná velikost výřezu v px
PER_PAGE = 100

os.makedirs(CACHE_DIR, exist_ok=True)

# Large source photos from Commons can exceed Pillow's default bomb limit.
Image.MAX_IMAGE_PIXELS = None


def _load_crops_from_db():
    """Build the CROPS list directly from crop_labels (no crops.json needed).

    This is the new-pipeline path: step 02 populates crop_labels straight from
    OSM, so the labeling UI reads from the DB instead of a crops.json dump.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Order by id only (insertion order), not "image, id": step 02 assigns
    # ids in the exact page order the UI should show (existing seed rows
    # first, new bolts appended strictly at the end), so sorting by image
    # here would re-alphabetize and shuffle bolts across pages every time
    # new images are added — breaking pagination stability.
    rows = conn.execute(
        "SELECT image, position, cx, cy, osm_source FROM crop_labels ORDER BY id"
    ).fetchall()
    conn.close()
    crops = []
    for r in rows:
        image = r["image"]
        file = image.removeprefix("File:")
        # crop_labels is already deduped by (image, position); the 1:n OSM
        # references live as a JSON array in osm_source. Emit one CROP per
        # reference so the BOLTS dedup below re-groups them with all sources.
        try:
            sources = json.loads(r["osm_source"]) if r["osm_source"] else []
        except (ValueError, TypeError):
            sources = []
        if not sources:
            sources = [{"osmId": 0, "osmType": "", "key": "", "order": 0}]
        for s in sources:
            crops.append({
                "image": image,
                "file": file,
                "pos": r["position"],
                "x": r["cx"], "y": r["cy"],
                "osmId": s.get("osmId", 0), "osmType": s.get("osmType", ""),
                "key": s.get("key", ""), "order": s.get("order", 0),
            })
    return crops


if os.path.isfile(CROPS_JSON):
    with open(CROPS_JSON, encoding="utf-8") as f:
        CROPS = json.load(f)
else:
    CROPS = _load_crops_from_db()

# Deduplikace: 1 fyzický bolt = (image, pos). Stejný bolt může pocházet z víc
# OSM prvků (cest) → sloučíme do jednoho záznamu se seznamem `sources`.
BOLTS = []
_bolt_idx = {}
for _c in CROPS:
    _k = (_c["image"], _c["pos"])
    _src = {"osmId": _c["osmId"], "osmType": _c["osmType"],
            "key": _c["key"], "order": _c["order"]}
    if _k in _bolt_idx:
        BOLTS[_bolt_idx[_k]]["sources"].append(_src)
    else:
        _bolt_idx[_k] = len(BOLTS)
        BOLTS.append({"file": _c["file"], "image": _c["image"], "pos": _c["pos"],
                      "x": _c["x"], "y": _c["y"], "sources": [_src]})

TOTAL = len(BOLTS)            # unikátních boltů
PAGES = max(1, math.ceil(TOTAL / PER_PAGE))
_BOLT_GLOBAL_IDX = {(b["image"], b["pos"]): i for i, b in enumerate(BOLTS)}

TEST_DATASET: list = []
if os.path.isfile(TEST_DATASET_FILE):
    with open(TEST_DATASET_FILE, encoding="utf-8") as f:
        TEST_DATASET = [json.loads(line) for line in f if line.strip()]

_TEST_RESULTS: dict = {}
if os.path.isfile(TEST_RESULTS_FILE):
    with open(TEST_RESULTS_FILE, encoding="utf-8") as f:
        _TEST_RESULTS = json.load(f)

# --- malá LRU cache dekódovaných zdrojových obrázků ---
_img_cache: dict[str, Image.Image] = {}
_img_order: list[str] = []
_img_lock = threading.Lock()
_IMG_CACHE_MAX = 6

# Omezení souběžného dekódování velkých JPEGů (proti OOM – stroj má 5.8 GB,
# 0 B swap; 100 paralelních dekódů ~70 MB/ks dřív server shodilo).
_decode_sem = threading.BoundedSemaphore(4)


def get_source(fname: str) -> Image.Image:
    with _img_lock:
        if fname in _img_cache:
            _img_order.remove(fname)
            _img_order.append(fname)
            return _img_cache[fname]
    img = Image.open(os.path.join(IMG_DIR, fname)).convert("RGB")
    img.load()
    with _img_lock:
        _img_cache[fname] = img
        _img_order.append(fname)
        while len(_img_order) > _IMG_CACHE_MAX:
            _img_cache.pop(_img_order.pop(0), None)
    return img


_dims_cache: dict[str, tuple] = {}
_dims_lock = threading.Lock()


def get_dims(fname: str) -> tuple:
    """(W, H) zdrojového obrázku – čte jen hlavičku JPEGu, s cache."""
    with _dims_lock:
        if fname in _dims_cache:
            return _dims_cache[fname]
    try:
        with Image.open(os.path.join(IMG_DIR, fname)) as im:
            wh = im.size
    except Exception:
        wh = (0, 0)
    with _dims_lock:
        _dims_cache[fname] = wh
    return wh


def make_crop(fname: str, x: float, y: float, half: int = HALF, out: int = OUT) -> bytes:
    safe = fname.replace("/", "_")
    suffix = f"_{half}" + (f"_{out}" if out != OUT else "")
    cache_file = os.path.join(CACHE_DIR, f"{safe}__{x}_{y}{suffix}.jpg")
    # back-compat: accept old cache files without _half suffix
    if half == HALF and out == OUT:
        old_file = os.path.join(CACHE_DIR, f"{safe}__{x}_{y}.jpg")
        if os.path.exists(old_file):
            with open(old_file, "rb") as f:
                return f.read()
    if os.path.exists(cache_file):
        with open(cache_file, "rb") as f:
            return f.read()

    # semafor drží počet souběžných dekódů velkých JPEGů nízko (RAM)
    with _decode_sem:
        if os.path.exists(cache_file):
            with open(cache_file, "rb") as f:
                return f.read()
        src = get_source(fname)
        W, H = src.size
        cx, cy = x * W, y * H
        left = max(0, int(round(cx - half))); top = max(0, int(round(cy - half)))
        box = (left, top, min(W, left + 2 * half), min(H, top + 2 * half))
        crop = src.crop(box).resize((out, out), Image.LANCZOS)

        buf = io.BytesIO()
        crop.save(buf, "JPEG", quality=85)
        data = buf.getvalue()
    with open(cache_file, "wb") as f:
        f.write(data)
    return data


# ---------------------------------------------------------- regenerace cache
# Stav běžící úlohy "purge & recreate". Jen jedna naráz.
_regen_lock = threading.Lock()
_regen_state = {
    "running": False,
    "total": 0,
    "done": 0,
    "errors": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}


def _regen_worker(workers: int):
    import glob
    import time
    import concurrent.futures
    try:
        # 1) smaž celou keš
        for f in glob.glob(os.path.join(CACHE_DIR, "*.jpg")):
            try:
                os.remove(f)
            except OSError:
                pass

        with _regen_lock:
            _regen_state.update(running=True, total=len(CROPS), done=0,
                                errors=0, started_at=time.time(),
                                finished_at=None, error=None)

        def task(c):
            try:
                make_crop(c["file"], c["x"], c["y"])
                return None
            except Exception as e:  # poškozený zdroj apod. – nezastavuj běh
                return str(e)

        # max `workers` souběžně (1 vCPU → drž load nízko)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(task, c) for c in CROPS]
            for fut in concurrent.futures.as_completed(futures):
                err = fut.result()
                with _regen_lock:
                    _regen_state["done"] += 1
                    if err:
                        _regen_state["errors"] += 1

        with _regen_lock:
            _regen_state["running"] = False
            _regen_state["finished_at"] = time.time()
    except Exception as e:
        with _regen_lock:
            _regen_state["running"] = False
            _regen_state["error"] = str(e)
            _regen_state["finished_at"] = time.time()


def start_regen(workers: int = 2) -> bool:
    """Spustí regeneraci na pozadí. Vrátí False, když už běží."""
    with _regen_lock:
        if _regen_state["running"]:
            return False
        _regen_state.update(running=True, total=len(CROPS), done=0,
                            errors=0, started_at=None, finished_at=None,
                            error=None)
    t = threading.Thread(target=_regen_worker, args=(workers,), daemon=True)
    t.start()
    return True


def regen_progress() -> dict:
    import time
    with _regen_lock:
        st = dict(_regen_state)
    done, total = st["done"], st["total"]
    eta = None
    rate = None
    if st["started_at"] and done > 0:
        end = st["finished_at"] or time.time()
        elapsed = end - st["started_at"]
        if elapsed > 0:
            rate = done / elapsed
            if st["running"] and rate > 0:
                eta = (total - done) / rate
    st["rate"] = rate
    st["eta"] = eta
    st["pct"] = (100.0 * done / total) if total else 0.0
    return st


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def global_stats() -> dict:
    """Globální čísla + schéma crop_labels pro patičku /crops."""
    conn = db()
    cur = conn.cursor()
    counts = {t: 0 for t in ("undecided", "no-bolt", "bolt")}
    for r in cur.execute("SELECT type, COUNT(*) n FROM crop_labels GROUP BY type"):
        counts[r["type"]] = r["n"]
    schema = [(r["name"], r["type"]) for r in
              cur.execute("PRAGMA table_info(crop_labels)")]
    conn.close()
    return {
        "total": TOTAL,
        "undecided": counts["undecided"],
        "nobolt": counts["no-bolt"],
        "bolts": counts["bolt"],
        "schema": schema,
        "db_file": os.path.abspath(DB_PATH),
        "table": "crop_labels",
    }


DETECTIONS_DB = os.path.join(HERE, "models", "detections_v1.sqlite")

def get_detections(filename: str) -> list:
    if not os.path.isfile(DETECTIONS_DB):
        return []
    try:
        conn = sqlite3.connect(DETECTIONS_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT cx, cy, w, h, score FROM detections WHERE image=? ORDER BY score DESC",
            (filename,)
        ).fetchall()
        conn.close()
        return [{"cx": r["cx"], "cy": r["cy"], "w": r["w"], "h": r["h"], "score": r["score"]}
                for r in rows]
    except Exception:
        return []

def get_photo_bolts(filename: str) -> dict:
    bolts_for_photo = [b for b in BOLTS if b["file"] == filename]
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT image, position, type, cx, cy, radius_px FROM crop_labels")
    label_map = {
        (r["image"], r["position"]): (r["type"], r["cx"], r["cy"], r["radius_px"])
        for r in cur.fetchall()
    }
    conn.close()
    bolts = []
    for b in bolts_for_photo:
        row = label_map.get((b["image"], b["pos"]))
        if row:
            typ, cx, cy, radius_px = row
            # použij upřesněné cx/cy ze sliderů; padni zpět na původní klik
            x = cx if cx is not None else b["x"]
            y = cy if cy is not None else b["y"]
        else:
            typ, x, y, radius_px = "undecided", b["x"], b["y"], None
        gidx = _BOLT_GLOBAL_IDX.get((b["image"], b["pos"]), 0)
        bolts.append({"x": x, "y": y,
                      "no_bolt": typ == "no-bolt", "type": typ,
                      "radius_px": radius_px, "pos": b["pos"],
                      "idx": gidx, "page": gidx // PER_PAGE + 1})
    return {"file": filename, "bolts": bolts}


def _render_precise_page_impl(base_path: str, dir_path: str, title: str) -> bytes:
    mf = os.path.join(dir_path, "_manifest.json")
    if os.path.isfile(mf):
        with open(mf, encoding="utf-8") as f:
            raw = json.load(f)
        # nový formát: list of dicts; starý formát: list of strings
        if raw and isinstance(raw[0], dict):
            entries = raw
        else:
            entries = [{"name": n, "page": None} for n in raw]
    else:
        entries = [{"name": n, "page": None}
                   for n in sorted(n for n in os.listdir(dir_path) if n.endswith(".jpg"))]

    def img_html(e):
        n = e["name"]
        img = (f'<img src="/{base_path}/{urllib.parse.quote(n)}" '
               f'title="{html.escape(n)}" loading="lazy">')
        if e.get("page"):
            anchor = f'#b{e["bolt_idx"]}' if e.get("bolt_idx") is not None else ""
            url = f'/crops?page={e["page"]}{anchor}'
            return f'<a href="{url}" title="Strana {e["page"]} v /crops">{img}</a>'
        return img

    imgs = "".join(img_html(e) for e in entries)
    doc = f"""<!DOCTYPE html><html lang="cs"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} – {len(entries)} boltů</title>
<style>
  body {{ margin:0; background:#111; color:#eee;
    font-family:system-ui,-apple-system,sans-serif; }}
  header {{ padding:10px 14px; background:#1c1c1c; border-bottom:1px solid #333;
    display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
  header h1 {{ font-size:15px; margin:0; }}
  header a {{ color:#7bf; text-decoration:none; font-size:14px; }}
  .wrap {{ padding:10px; font-size:0; }}
  .wrap img {{ width:50px; height:50px; object-fit:fill; display:inline-block;
    margin:1px; background:#000; image-rendering:pixelated;
    outline:1px solid #333; }}
  .wrap img:hover {{ outline:2px solid #3d3; position:relative; z-index:1; }}
  .note {{ padding:0 14px 10px; color:#000; font-size:13px; }}
</style></head><body>
<header>
  <h1>{html.escape(title)} – {len(entries)} ks (1:1 pixely, zobrazeno 50×50)</h1>
  <a href="/crops?show=bolt">← /crops</a>
</header>
<div class="note">Každý čtvereček = přesný výřez boxu (cx±r, cy±r), co jde do tréninku.
  Upscalováno na 50×50 (pixelated), takže vidíš skutečné pixely.</div>
<div class="wrap">{imgs}</div>
</body></html>"""
    return doc.encode("utf-8")


def render_precise_page() -> bytes:
    return _render_precise_page_impl(
        "crops-precise-571", PRECISE_DIR, "Přesné boxy trénovacích boltů (571)")


def render_precise_full_page() -> bytes:
    return _render_precise_page_impl(
        "crops-precise-full", PRECISE_FULL_DIR, "Přesné boxy trénovacích boltů (full)")


def render_debug() -> bytes:
    # počty detekcí z DB
    det_counts: dict[str, int] = {}
    if os.path.isfile(DETECTIONS_DB):
        try:
            _dc = sqlite3.connect(DETECTIONS_DB)
            for row in _dc.execute("SELECT image, COUNT(*) as n FROM detections GROUP BY image"):
                det_counts[row[0]] = row[1]
            _dc.close()
        except Exception:
            pass

    # počty crops (anotací) per foto
    crop_counts: dict[str, int] = {}
    for c in CROPS:
        crop_counts[c["file"]] = crop_counts.get(c["file"], 0) + 1

    photos = sorted({c["file"] for c in CROPS},
                    key=lambda p: det_counts.get(p, 0), reverse=True)
    photo_opts = "".join(
        f'<option value="{html.escape(p)}">{det_counts.get(p, 0)}/{crop_counts.get(p, 0)} {html.escape(p)}</option>'
        for p in photos
    )
    doc = r"""<!DOCTYPE html><html lang="cs"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>YOLO Debug – inferenční náhled</title>
<style>
:root{--bg:#111;--fg:#eee;--panel:#1c1c1c;--accent:#4af;--green:#3d3;--red:#f44}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,sans-serif;display:flex;flex-direction:column;height:100vh;overflow:hidden}
header{flex:0 0 auto;background:var(--panel);border-bottom:1px solid #333;padding:8px 12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
header h1{font-size:14px;flex:1 1 auto}
header a.btn,header button.btn{background:#2a2a2a;color:var(--fg);border:1px solid #444;padding:6px 11px;border-radius:6px;font-size:13px;cursor:pointer;text-decoration:none;white-space:nowrap}
#main{flex:1 1 auto;display:flex;min-height:0;overflow:hidden}
#sidebar{flex:0 0 220px;overflow-y:auto;border-right:1px solid #333;background:#151515;display:flex;flex-direction:column}
#sidebar select{flex:1 1 auto;background:#1a1a1a;color:var(--fg);border:none;padding:4px;font-size:12px;min-height:200px;outline:none}
#sidebar select option{padding:3px 6px}
#sidebar select option:hover,#sidebar select option:checked{background:#2a3a4a}
#canvas-area{flex:1 1 auto;overflow:hidden;position:relative;touch-action:none;background:#0a0a0a}
canvas{position:absolute;top:0;left:0;display:block;transform-origin:0 0;will-change:transform;image-rendering:auto}
#zoom-ctl{position:absolute;right:10px;bottom:10px;z-index:10;display:flex;gap:6px}
#zoom-ctl button{width:38px;height:38px;border-radius:8px;background:rgba(40,40,40,.85);border:1px solid #555;color:#fff;font-size:20px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;line-height:1}
#zoom-ctl button:active{background:rgba(80,80,80,.9)}
#zoom-pct{position:absolute;left:10px;bottom:10px;z-index:10;font-size:11px;color:#000;background:rgba(20,20,20,.7);padding:3px 7px;border-radius:5px}
#right{flex:0 0 260px;display:flex;flex-direction:column;border-left:1px solid #333;background:#151515;overflow:hidden}
#controls{padding:10px 12px;border-bottom:1px solid #333;display:flex;flex-direction:column;gap:8px}
#infer-btn{width:100%;padding:14px;background:#1a4a8a;border:1px solid #3a6aaa;color:#fff;border-radius:8px;font-size:16px;font-weight:700;cursor:pointer;transition:.15s}
#infer-btn:hover{background:#2a5aaa}
#infer-btn:disabled{opacity:.5;cursor:not-allowed}
#conf-row{display:flex;align-items:center;gap:6px;font-size:12px;color:#aaa}
#conf-row input{flex:1;accent-color:var(--accent)}
#toggle-nobolt,#toggle-undecided{display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;user-select:none}
#toggle-nobolt input{width:16px;height:16px;cursor:pointer;accent-color:#f44}
#toggle-undecided input{width:16px;height:16px;cursor:pointer;accent-color:#000}
#timing{padding:10px 12px;flex:1 1 auto;overflow-y:auto;font-size:12px;color:#aaa;font-family:monospace}
#timing h3{color:#ccc;font-size:12px;margin-bottom:6px;font-family:system-ui}
.trow{display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #222}
.trow span:last-child{color:#7cf;text-align:right}
#detections{padding:10px 12px;border-top:1px solid #333;font-size:12px;color:#aaa;max-height:200px;overflow-y:auto}
#detections h3{color:#ccc;font-size:12px;margin-bottom:6px;font-family:system-ui}
.det{padding:2px 0;border-bottom:1px solid #222;font-family:monospace}
.legend{display:flex;gap:10px;flex-wrap:wrap;font-size:11px;padding:6px 12px;border-bottom:1px solid #333}
.litem{display:flex;align-items:center;gap:4px}
.ldot{width:10px;height:10px;border-radius:50%}
#status-bar{flex:0 0 auto;background:#0a0a0a;border-top:1px solid #222;padding:4px 10px;font-size:11px;color:#666}
@media(max-width:700px){
  #main{flex-direction:column;overflow:auto}
  #sidebar{flex:0 0 auto;max-height:120px}
  #sidebar select{min-height:80px}
  #right{flex:0 0 auto;border-left:none;border-top:1px solid #333}
  /* canvas je position:absolute → area potřebuje explicitní výšku, jinak zkolabuje */
  #canvas-area{flex:0 0 auto;height:60vh}
  body{height:auto;overflow:auto}
}
</style>
</head><body>
<header>
  <h1>YOLO Debug – borháky</h1>
  <a class="btn" href="/crops">← /crops</a>
</header>
<div id="main">
  <div id="sidebar">
    <select id="photo-list" size="20">""" + photo_opts + r"""</select>
  </div>
  <div id="canvas-area">
    <canvas id="cv"></canvas>
    <div id="zoom-pct">100%</div>
    <div id="zoom-ctl">
      <button id="zoom-out" title="Oddálit">−</button>
      <button id="zoom-fit" title="Přizpůsobit" style="font-size:14px">⤢</button>
      <button id="zoom-in" title="Přiblížit">+</button>
    </div>
  </div>
  <div id="right">
    <div class="legend">
      <div class="litem"><div class="ldot" style="background:#3d3;border:2px solid #3d3"></div> bolt</div>
      <div class="litem"><div class="ldot" style="background:none;border:2px solid #000"></div> undecided</div>
      <div class="litem"><div class="ldot" style="background:none;border:2px solid #f44"></div> no-bolt</div>
      <div class="litem"><div class="ldot" style="background:none;border:2px solid #ff0"></div> YOLO detekce</div>
    </div>
    <div id="controls">
      <button id="infer-btn" onclick="runInference()" disabled>Spustit YOLO inferenci</button>
      <div id="conf-row">
        <span>Conf:</span>
        <input type="range" id="conf-slider" min="1" max="95" value="25" oninput="document.getElementById('conf-val').textContent=this.value+'%'">
        <span id="conf-val">25%</span>
      </div>
      <label id="toggle-nobolt">
        <input type="checkbox" id="show-nobolt" checked onchange="drawOverlay()">
        Zobrazit no-bolt anotace
      </label>
      <label id="toggle-undecided">
        <input type="checkbox" id="show-undecided" checked onchange="drawOverlay()">
        Zobrazit undecided anotace
      </label>
    </div>
    <div id="timing">
      <h3>Časy inference</h3>
      <div id="timing-rows"><div style="color:#555">– zatím nespuštěno –</div></div>
    </div>
    <div id="detections">
      <h3>Detekce</h3>
      <div id="det-rows"><div style="color:#555">–</div></div>
    </div>
  </div>
</div>
<div id="status-bar" id="sbar">Vyberte fotografii ze seznamu vlevo</div>
<script>
// ── State ──────────────────────────────────────────────────────────────────
let currentFile = null;
let photoImg = null;
let photoBolts = [];
let yoloBoxes = [];   // [{cx,cy,w,h,score}] in original image coords
let session = null;
let modelLoading = false;

const cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
const sbar = document.getElementById('status-bar');

// ── Zoom / pan (CSS transform na canvasu) ───────────────────────────────────
const area = document.getElementById('canvas-area');
const zoomPct = document.getElementById('zoom-pct');
let zScale = 1, zTx = 0, zTy = 0, zFit = 1;

function applyTransform() {
  cv.style.transform = `translate(${zTx}px, ${zTy}px) scale(${zScale})`;
  zoomPct.textContent = Math.round(zScale / zFit * 100) + '%';
}

function fitToView() {
  if (!cv.width || !cv.height) return;
  const aw = area.clientWidth, ah = area.clientHeight;
  zFit = Math.min(aw / cv.width, ah / cv.height);
  zScale = zFit;
  zTx = (aw - cv.width * zScale) / 2;
  zTy = (ah - cv.height * zScale) / 2;
  applyTransform();
}

// zoom kolem bodu (px, py) v souřadnicích area
function zoomAt(px, py, factor) {
  const newScale = Math.max(zFit * 0.5, Math.min(zFit * 40, zScale * factor));
  const k = newScale / zScale;
  zTx = px - (px - zTx) * k;
  zTy = py - (py - zTy) * k;
  zScale = newScale;
  applyTransform();
}

// ── desktop: kolečko ──
area.addEventListener('wheel', (e) => {
  e.preventDefault();
  const r = area.getBoundingClientRect();
  zoomAt(e.clientX - r.left, e.clientY - r.top, e.deltaY < 0 ? 1.15 : 1/1.15);
}, { passive: false });

// ── tlačítka ──
document.getElementById('zoom-in').onclick  = () => zoomAt(area.clientWidth/2, area.clientHeight/2, 1.4);
document.getElementById('zoom-out').onclick = () => zoomAt(area.clientWidth/2, area.clientHeight/2, 1/1.4);
document.getElementById('zoom-fit').onclick = () => fitToView();

// ── pointer pan + pinch (touch i myš) ──
const pointers = new Map();
let pinchPrevDist = 0, pinchMid = null;

area.addEventListener('pointerdown', (e) => {
  area.setPointerCapture(e.pointerId);
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
});

area.addEventListener('pointermove', (e) => {
  if (!pointers.has(e.pointerId)) return;
  const prev = pointers.get(e.pointerId);
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  const r = area.getBoundingClientRect();

  if (pointers.size === 1) {
    // pan
    zTx += e.clientX - prev.x;
    zTy += e.clientY - prev.y;
    applyTransform();
  } else if (pointers.size === 2) {
    // pinch zoom
    const pts = [...pointers.values()];
    const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
    const midX = (pts[0].x + pts[1].x) / 2 - r.left;
    const midY = (pts[0].y + pts[1].y) / 2 - r.top;
    if (pinchPrevDist > 0) zoomAt(midX, midY, dist / pinchPrevDist);
    pinchPrevDist = dist;
  }
});

function endPointer(e) {
  pointers.delete(e.pointerId);
  if (pointers.size < 2) pinchPrevDist = 0;
}
area.addEventListener('pointerup', endPointer);
area.addEventListener('pointercancel', endPointer);
area.addEventListener('pointerleave', endPointer);

// ── double-tap / double-click toggle zoom ──
let lastTap = 0;
area.addEventListener('dblclick', (e) => {
  const r = area.getBoundingClientRect();
  zoomAt(e.clientX - r.left, e.clientY - r.top, zScale > zFit * 1.5 ? zFit / zScale : 3);
});
area.addEventListener('pointerup', (e) => {
  const now = Date.now();
  if (now - lastTap < 300 && e.pointerType === 'touch') {
    const r = area.getBoundingClientRect();
    zoomAt(e.clientX - r.left, e.clientY - r.top, zScale > zFit * 1.5 ? zFit / zScale : 3);
  }
  lastTap = now;
});

window.addEventListener('resize', () => fitToView());

// ── Photo loading ──────────────────────────────────────────────────────────
async function loadPhoto(fname) {
  if (!fname) return;
  if (fname === currentFile) return;   // už načteno – neopakovat fetch
  currentFile = fname;
  history.replaceState(null, '', '/debug?photo=' + encodeURIComponent(fname));
  yoloBoxes = [];
  sbar.textContent = 'Načítám fotografii…';
  document.getElementById('det-rows').innerHTML = '<div style="color:#555">–</div>';

  const enc = encodeURIComponent(fname);
  const [img, boltsData, detData] = await Promise.all([
    new Promise((res, rej) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = rej;
      i.src = '/images/' + enc;
    }),
    fetch('/api/photo-bolts?file=' + enc).then(r => r.json()),
    fetch('/api/detections?file=' + enc).then(r => r.json()).catch(() => ({boxes:[]}))
  ]);

  photoImg = img;
  photoBolts = boltsData.bolts || [];
  yoloBoxes = detData.boxes || [];
  cv.width = img.naturalWidth;
  cv.height = img.naturalHeight;
  drawOverlay();
  fitToView();
  const detInfo = yoloBoxes.length ? `  |  ${yoloBoxes.length} det (DB)` : '';
  sbar.textContent = fname + '  (' + img.naturalWidth + '×' + img.naturalHeight + ')  |  ' + photoBolts.length + ' anotací' + detInfo;
  if (session) document.getElementById('infer-btn').disabled = false;
}

function drawOverlay() {
  if (!photoImg) return;
  ctx.drawImage(photoImg, 0, 0);
  const showNoBolt = document.getElementById('show-nobolt').checked;
  const showUndecided = document.getElementById('show-undecided').checked;
  const r = Math.max(18, Math.min(photoImg.naturalWidth, photoImg.naturalHeight) * 0.012);

  // YOLO detections (pod anotacemi)
  const conf = document.getElementById('conf-slider').value / 100;
  for (let i = 0; i < yoloBoxes.length; i++) {
    const box = yoloBoxes[i];
    if (box.score < conf) continue;
    if (i === _hoveredBox) continue;
    const x = (box.cx - box.w / 2) * photoImg.naturalWidth;
    const y = (box.cy - box.h / 2) * photoImg.naturalHeight;
    const w = box.w * photoImg.naturalWidth;
    const h = box.h * photoImg.naturalHeight;
    ctx.strokeStyle = '#ff0';
    ctx.lineWidth = Math.max(3, r * 0.18);
    ctx.strokeRect(x, y, w, h);
    ctx.fillStyle = 'rgba(255,220,0,0.85)';
    ctx.font = `bold ${Math.max(14, r * 0.8)}px monospace`;
    ctx.fillText((box.score * 100).toFixed(1) + '%', x + 4, y + Math.max(14, r * 0.8) + 2);
  }

  // dataset annotations (navrch, aby je žluté boxy nepřekryly)
  for (const b of photoBolts) {
    const isNoBolt = b.type === 'no-bolt';
    const isUndecided = b.type === 'undecided' || (!b.type && !isNoBolt);
    if (isNoBolt && !showNoBolt) continue;
    if (isUndecided && !showUndecided) continue;
    const px = b.x * photoImg.naturalWidth;
    const py = b.y * photoImg.naturalHeight;
    ctx.beginPath();
    ctx.arc(px, py, r, 0, Math.PI * 2);
    ctx.strokeStyle = isNoBolt ? '#f44' : (isUndecided ? '#000' : '#3d3');
    ctx.lineWidth = Math.max(3, r * 0.18);
    ctx.stroke();
  }
}

// conf slider live redraw
document.getElementById('conf-slider').addEventListener('input', drawOverlay);

// hover nad YOLO boxem → skryj ho
let _hoveredBox = -1;
area.addEventListener('mousemove', (e) => {
  if (!photoImg || !yoloBoxes.length) return;
  const ar = area.getBoundingClientRect();
  const mx = (e.clientX - ar.left - zTx) / zScale;
  const my = (e.clientY - ar.top  - zTy) / zScale;
  const W = photoImg.naturalWidth, H = photoImg.naturalHeight;
  const conf = document.getElementById('conf-slider').value / 100;
  let found = -1;
  for (let i = 0; i < yoloBoxes.length; i++) {
    const box = yoloBoxes[i];
    if (box.score < conf) continue;
    const x1 = (box.cx - box.w / 2) * W, y1 = (box.cy - box.h / 2) * H;
    const x2 = (box.cx + box.w / 2) * W, y2 = (box.cy + box.h / 2) * H;
    if (mx >= x1 && mx <= x2 && my >= y1 && my <= y2) { found = i; break; }
  }
  if (found !== _hoveredBox) { _hoveredBox = found; drawOverlay(); }
});
area.addEventListener('mouseleave', () => {
  if (_hoveredBox !== -1) { _hoveredBox = -1; drawOverlay(); }
});

// klik na canvas → přejdi na bolt v /crops (otevři v nové záložce)
let _pdownPos = null;
area.addEventListener('pointerdown', (e) => { _pdownPos = {x:e.clientX,y:e.clientY}; });
area.addEventListener('click', (e) => {
  if (!photoImg || !photoBolts.length) return;
  if (_pdownPos && Math.hypot(e.clientX-_pdownPos.x, e.clientY-_pdownPos.y) > 8) return;
  const ar = area.getBoundingClientRect();
  const cx = (e.clientX - ar.left - zTx) / zScale;
  const cy = (e.clientY - ar.top  - zTy) / zScale;
  const W = photoImg.naturalWidth, H = photoImg.naturalHeight;
  const rAnn = Math.max(18, Math.min(W,H) * 0.012);
  let best = null, bestDist = Infinity;
  for (const b of photoBolts) {
    const dist = Math.hypot(cx - b.x*W, cy - b.y*H);
    if (dist < rAnn * 2 && dist < bestDist) { bestDist = dist; best = b; }
  }
  if (best && best.idx !== undefined) {
    window.open('/crops?page='+best.page+'#b'+best.idx, '_blank');
  }
});

// ── ONNX model ─────────────────────────────────────────────────────────────
async function ensureModel() {
  if (session) return session;
  if (modelLoading) return null;
  modelLoading = true;
  sbar.textContent = 'Stahuji model (12 MB)…';
  try {
    const ort = window.ort;
    ort.env.wasm.numThreads = 1;
    session = await ort.InferenceSession.create('/model/best.onnx', {
      executionProviders: ['wasm']
    });
    sbar.textContent = 'Model načten.';
    if (currentFile) document.getElementById('infer-btn').disabled = false;
  } catch(e) {
    sbar.textContent = 'Chyba načítání modelu: ' + e;
  }
  modelLoading = false;
  return session;
}

// ── Letterbox ──────────────────────────────────────────────────────────────
function letterbox(img, size) {
  const sc = Math.min(size / img.naturalWidth, size / img.naturalHeight);
  const nw = Math.round(img.naturalWidth * sc);
  const nh = Math.round(img.naturalHeight * sc);
  const padX = Math.floor((size - nw) / 2);
  const padY = Math.floor((size - nh) / 2);
  const off = document.createElement('canvas');
  off.width = size; off.height = size;
  const oc = off.getContext('2d');
  oc.fillStyle = '#808080';
  oc.fillRect(0, 0, size, size);
  oc.drawImage(img, padX, padY, nw, nh);
  return { canvas: off, padX, padY, sc };
}

// ── NMS ────────────────────────────────────────────────────────────────────
function nms(boxes, iouThresh) {
  boxes = boxes.slice().sort((a, b) => b.score - a.score);
  const keep = [];
  const used = new Uint8Array(boxes.length);
  for (let i = 0; i < boxes.length; i++) {
    if (used[i]) continue;
    keep.push(boxes[i]);
    for (let j = i + 1; j < boxes.length; j++) {
      if (used[j]) continue;
      if (iou(boxes[i], boxes[j]) > iouThresh) used[j] = 1;
    }
  }
  return keep;
}
function iou(a, b) {
  const ax1 = a.cx - a.w/2, ay1 = a.cy - a.h/2, ax2 = a.cx + a.w/2, ay2 = a.cy + a.h/2;
  const bx1 = b.cx - b.w/2, by1 = b.cy - b.h/2, bx2 = b.cx + b.w/2, by2 = b.cy + b.h/2;
  const ix = Math.max(0, Math.min(ax2,bx2) - Math.max(ax1,bx1));
  const iy = Math.max(0, Math.min(ay2,by2) - Math.max(ay1,by1));
  const inter = ix * iy;
  return inter / (a.w*a.h + b.w*b.h - inter);
}

// ── Tiling: pozice dlaždic ───────────────────────────────────────────────────
const TILE = 1024;       // velikost dlaždice = vstup modelu (trénováno na 1024)
const OVERLAP = 0.20;
function tilePositions(size, tile, stride) {
  if (size <= tile) return [0];
  const pos = [];
  for (let p = 0; p <= size - tile; p += stride) pos.push(p);
  if (pos[pos.length - 1] !== size - tile) pos.push(size - tile);
  return pos;
}

// jedna dlaždice → tensor [1,3,TILE,TILE] (nativní pixely, žádný downscale)
function tileTensor(img, tx, ty, tw, th) {
  const off = document.createElement('canvas');
  off.width = TILE; off.height = TILE;
  const oc = off.getContext('2d');
  oc.fillStyle = '#808080';
  oc.fillRect(0, 0, TILE, TILE);
  // výřez umístíme do levého horního rohu (1:1, bez změny měřítka)
  oc.drawImage(img, tx, ty, tw, th, 0, 0, tw, th);
  const d = oc.getImageData(0, 0, TILE, TILE).data;
  const n = TILE * TILE;
  const t = new Float32Array(3 * n);
  for (let i = 0; i < n; i++) {
    t[i]       = d[i*4]   / 255;
    t[n+i]     = d[i*4+1] / 255;
    t[2*n+i]   = d[i*4+2] / 255;
  }
  return t;
}

// ── Inference (tiling – po segmentech v nativním rozlišení) ──────────────────
async function runInference() {
  if (!photoImg) { sbar.textContent = 'Nejdřív vyberte fotografii.'; return; }
  const sess = await ensureModel();
  if (!sess) return;

  const btn = document.getElementById('infer-btn');
  btn.disabled = true;

  const W = photoImg.naturalWidth, H = photoImg.naturalHeight;
  const confThresh = document.getElementById('conf-slider').value / 100;
  const ort = window.ort;
  const inputName = sess.inputNames[0];
  const stride = Math.round(TILE * (1 - OVERLAP));
  const xs = tilePositions(W, TILE, stride);
  const ys = tilePositions(H, TILE, stride);
  const nTiles = xs.length * ys.length;

  const T = { pre: 0, inf: 0 };
  const t0 = performance.now();
  const raw = [];

  try {
    let done = 0;
    for (const ty of ys) {
      for (const tx of xs) {
        const tw = Math.min(TILE, W - tx), th = Math.min(TILE, H - ty);

        const tp0 = performance.now();
        const tensor = tileTensor(photoImg, tx, ty, tw, th);
        const tp1 = performance.now(); T.pre += tp1 - tp0;

        const input = new ort.Tensor('float32', tensor, [1, 3, TILE, TILE]);
        const out = await sess.run({ [inputName]: input });
        const tp2 = performance.now(); T.inf += tp2 - tp1;

        const od = out[sess.outputNames[0]];
        const data = od.data, n_anch = od.dims[2];
        for (let i = 0; i < n_anch; i++) {
          const score = data[4 * n_anch + i];
          if (score < confThresh) continue;
          // tile je 1:1 v levém horním rohu → pixel dlaždice = global px (tx+.., ty+..)
          const gx = tx + data[0 * n_anch + i];
          const gy = ty + data[1 * n_anch + i];
          const gw = data[2 * n_anch + i];
          const gh = data[3 * n_anch + i];
          // odfiltruj detekce z šedé výplně (mimo skutečný výřez)
          if (data[0*n_anch+i] > tw || data[1*n_anch+i] > th) continue;
          raw.push({ cx: gx / W, cy: gy / H, w: gw / W, h: gh / H, score });
        }
        done++;
        btn.textContent = `Inferuji… ${done}/${nTiles}`;
        sbar.textContent = `Dlaždice ${done}/${nTiles} · zatím ${raw.length} kandidátů`;
        await new Promise(r => setTimeout(r, 0));  // nech UI dýchat
      }
    }

    const tNms0 = performance.now();
    const boxes = nms(raw, 0.45);
    const tNms1 = performance.now();
    yoloBoxes = boxes;
    drawOverlay();

    const total = tNms1 - t0;
    const rows = [
      ['Dlaždic', nTiles + ' × ' + TILE + 'px'],
      ['Preprocess', T.pre.toFixed(0) + ' ms'],
      ['ONNX inference', T.inf.toFixed(0) + ' ms'],
      ['NMS', (tNms1 - tNms0).toFixed(1) + ' ms'],
      ['Celkem', total.toFixed(0) + ' ms'],
      ['Na dlaždici', (T.inf / nTiles).toFixed(0) + ' ms'],
    ];
    document.getElementById('timing-rows').innerHTML = rows.map(([k, v]) =>
      `<div class="trow"><span>${k}</span><span>${v}</span></div>`).join('');

    const shown = boxes.filter(b => b.score >= confThresh);
    document.getElementById('det-rows').innerHTML = shown.length
      ? shown.map((b,i) => `<div class="det">#${i+1} score=${(b.score*100).toFixed(1)}%  cx=${b.cx.toFixed(4)} cy=${b.cy.toFixed(4)}</div>`).join('')
      : '<div style="color:#888">Žádné detekce nad prahem</div>';

    sbar.textContent = `Inference hotová – ${shown.length} detekcí z ${nTiles} dlaždic (${total.toFixed(0)} ms)`;
  } catch(e) {
    sbar.textContent = 'Chyba inference: ' + e;
    console.error(e);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Spustit YOLO inferenci';
  }
}

// ── Init: load onnxruntime-web then auto-load model ─────────────────────────
(function() {
  const s = document.createElement('script');
  s.src = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.18.0/dist/ort.min.js';
  s.onload = () => ensureModel();
  s.onerror = () => { sbar.textContent = 'Nelze načíst onnxruntime-web ze CDN.'; };
  document.head.appendChild(s);

  // ── Photo select: bind change + click (listbox swallows onchange on re-click)
  const sel = document.getElementById('photo-list');
  const onPick = () => { if (sel.value) loadPhoto(sel.value); };
  sel.addEventListener('change', onPick);
  sel.addEventListener('click', onPick);
  sel.addEventListener('keyup', onPick);

  // ── Pick initial photo: ?photo= from URL, else first ──────────────────────
  if (sel.options.length) {
    const want = new URLSearchParams(location.search).get('photo');
    let target = sel.options[0];
    if (want) {
      for (const o of sel.options) {
        if (o.value === want) { target = o; break; }
      }
    }
    target.selected = true;
    target.scrollIntoView({ block: 'nearest' });
    loadPhoto(target.value);
  }
})();
</script>
</body></html>"""
    return doc.encode("utf-8")


# ---------------------------------------------------------------- HTML stránky

DEFAULT_RADIUS_PX = 10
CROP_SCALE = OUT / (2 * HALF)   # thumbnail px na 1 orig px (200/40 = 5), stejné pro obě velikosti
BIG_HALF = HALF * 2             # 2x víc pixelů okolí (±40 px), stejný SCALE → 2x větší náhled
BIG_OUT = OUT * 2
HUGE_HALF = HALF * 4            # 4x víc pixelů okolí (±80 px) → 4x větší náhled
HUGE_OUT = OUT * 4


def render_crops(page: int, filter_results: str = "", show: str = "", size: str = "") -> bytes:
    size = "huge" if size == "huge" else ("big" if size == "big" else "normal")
    halfv = HUGE_HALF if size == "huge" else (BIG_HALF if size == "big" else HALF)
    outv  = HUGE_OUT  if size == "huge" else (BIG_OUT  if size == "big" else OUT)
    clampv = halfv - 2
    # -- mapa typů/geometrie z crop_labels (klíč = image, position) -----------
    conn = db(); cur = conn.cursor()
    lmap = {
        (r["image"], r["position"]):
            (r["type"], r["cx"], r["cy"], r["radius_px"])
        for r in cur.execute(
            "SELECT image, position, type, cx, cy, radius_px FROM crop_labels")
    }
    conn.close()

    def typ_of(b):
        return lmap.get((b["image"], b["pos"]),
                        ("undecided", b["x"], b["y"], None))

    # -- pool = PEVNÝ seznam unikátních boltů → stabilní strany ----------------
    # `show` (typ) NEredukuje pool, jen skryje nesedící buňky. Tím se při úpravě
    # typu nikdy neposunou hranice stránek a nic se „nepřelije" z vedlejší strany.
    if filter_results:
        rf_rows, _ = _load_result_file(filter_results, 80)
        n_set = {(c["file"], c["pos"]) for c, res in rf_rows if res == "N"}
        pool = [b for b in BOLTS if (b["file"], b["pos"]) in n_set]
    else:
        pool = BOLTS

    total = len(pool)
    pages = max(1, math.ceil(total / PER_PAGE))
    page = max(1, min(page, pages))
    start = (page - 1) * PER_PAGE
    chunk = pool[start:start + PER_PAGE]
    show_active = show in ("undecided", "no-bolt", "bolt")
    visible_count = 0
    # -------------------------------------------------------------------------

    dims = {f: get_dims(f) for f in {b["file"] for b in chunk}}
    cls_map = {"no-bolt": "t-nobolt", "bolt": "t-bolt"}

    cells = []
    for b in chunk:
        typ, lcx, lcy, lr = typ_of(b)
        cls = cls_map.get(typ, "t-undecided")
        if show_active and typ != show:
            cls += " filtered-out"
        else:
            visible_count += 1
        global_idx = _BOLT_GLOBAL_IDX.get((b["image"], b["pos"]), 0)
        W, H = dims.get(b["file"], (0, 0))
        r = lr if lr else DEFAULT_RADIUS_PX
        # posun uloženého středu od kliku (orig px); klik = střed náhledu
        dx = (lcx - b["x"]) * W if (lcx is not None and W) else 0.0
        dy = (lcy - b["y"]) * H if (lcy is not None and H) else 0.0
        cxv = outv / 2 + dx * CROP_SCALE
        cyv = outv / 2 + dy * CROP_SCALE
        rv = r * CROP_SCALE

        img_url = "/crop-img?" + urllib.parse.urlencode(
            {"file": b["file"], "x": b["x"], "y": b["y"], "half": halfv, "out": outv})
        # identita = (image, position); sources = všechny OSM prvky tohoto boltu
        meta = json.dumps({
            "image": b["image"], "position": b["pos"], "sources": b["sources"],
        })
        orders = sorted({s["order"] for s in b["sources"]})
        nsrc = len(b["sources"])
        order_lbl = "#" + ",".join(str(o) for o in orders)
        label = f'{html.escape(b["file"])} · {order_lbl} · {html.escape(b["pos"])}'
        src0 = b["sources"][0]
        sub = (f'{html.escape(src0["key"])} · osm {src0["osmId"]}'
               + (f' <i>+{nsrc-1} zdrojů</i>' if nsrc > 1 else ''))
        view_url = "/view?" + urllib.parse.urlencode(
            {"file": b["file"], "bx": b["x"], "by": b["y"], "pos": b["pos"]})

        cells.append(
            f'<figure class="cell {cls}" id="b{global_idx}" '
            f"data-meta='{html.escape(meta)}' "
            f'data-x="{b["x"]}" data-y="{b["y"]}" data-w="{W}" data-h="{H}">'
            f'<div class="imgwrap">'
            f'<img loading="lazy" src="{img_url}" width="{outv}" height="{outv}" alt="">'
            f'<svg class="ovl" viewBox="0 0 {outv} {outv}">'
            f'<circle cx="{cxv:.1f}" cy="{cyv:.1f}" r="{rv:.1f}"/></svg>'
            f'<div class="zone zone-left" title="Potvrdit bolt"></div>'
            f'<div class="zone zone-right" title="Označit no-bolt"></div>'
            f'<span class="badge badge-yes">✓</span>'
            f'<span class="badge badge-no">✕</span></div>'
            f'<div class="sliders" data-r="{r}" data-dx="{dx:.2f}" data-dy="{dy:.2f}">'
            f'<label>r<input type="range" class="s-r" min="2" max="30" step="0.5" value="{r}"></label>'
            f'<label>x<input type="range" class="s-x" min="-{clampv}" max="{clampv}" step="0.5" value="{dx:.2f}"></label>'
            f'<label>y<input type="range" class="s-y" min="-{clampv}" max="{clampv}" step="0.5" value="{dy:.2f}"></label>'
            f'</div>'
            f'<a class="cap" href="{html.escape(view_url)}" '
            f'title="Otevřít celý obrázek se zvýrazněným borhákem">'
            f'<b>{label}</b><span>{sub}</span></a>'
            f"</figure>"
        )

    # pager links preserve filter + show + size params
    fqs = ""
    if filter_results:
        fqs += f"&filter={urllib.parse.quote(filter_results)}"
    if show:
        fqs += f"&show={urllib.parse.quote(show)}"
    if size in ("big", "huge"):
        fqs += f"&size={size}"

    show_label = {"undecided": " · ? jen nevíme",
                  "no-bolt": " · ✗ jen no-bolt",
                  "bolt": " · ✓ jen potvrzené bolty"}.get(show, "")

    def pager():
        prev_d = "disabled" if page == 1 else ""
        next_d = "disabled" if page == pages else ""
        label = (f'výřezy {start+1}–{min(start+PER_PAGE, total)} z {total}'
                 + (f' <span style="color:#fa8">(filtr: {html.escape(filter_results)})</span>'
                    if filter_results else '')
                 + (f' <span style="color:#8cf">{html.escape(show_label)}'
                    f' · {visible_count} na straně</span>'
                    if show_label else ''))
        return (
            f'<div class="pager">'
            f'<a class="btn {prev_d}" href="/crops?page={page-1}{fqs}">‹ Předchozí</a>'
            f'<span>Strana <b>{page}</b> / {pages} · {label}</span>'
            f'<a class="btn {next_d}" href="/crops?page={page+1}{fqs}">Další ›</a>'
            f'<form method="get" action="/crops" class="jump">'
            + (f'<input type="hidden" name="filter" value="{html.escape(filter_results)}">'
               if filter_results else '')
            + (f'<input type="hidden" name="show" value="{html.escape(show)}">'
               if show else '')
            + (f'<input type="hidden" name="size" value="{size}">'
               if size in ("big", "huge") else '')
            + f'<input type="number" name="page" min="1" max="{pages}" value="{page}">'
            f'<button class="btn">Jdi</button></form>'
            f"</div>"
        )

    sqs = f"&size={size}" if size in ("big", "huge") else ""

    filter_opts = "".join(
        f'<option value="{html.escape(f)}"{"  selected" if f == filter_results else ""}>'
        f'{html.escape(f)}</option>'
        for f in _list_result_files()
    )

    # -- globální statistika do patičky -----------------------------------------
    gs = global_stats()
    schema_html = ", ".join(
        f'<code>{html.escape(n)}</code> <i>{html.escape(t)}</i>'
        for n, t in gs["schema"]
    )
    stats_html = (
        '<div class="stats">'
        '<div class="srow">'
        f'<span>Cropů celkem: <b>{gs["total"]}</b></span>'
        f'<span>? <i>nevíme</i>: <b>{gs["undecided"]}</b></span>'
        f'<span>✗ <i>no-bolt</i>: <b>{gs["nobolt"]}</b></span>'
        f'<span>✓ <i>potvrzené bolty</i>: <b>{gs["bolts"]}</b></span>'
        '</div>'
        f'<div class="srow"><span>DB soubor: <code>{html.escape(gs["db_file"])}</code></span>'
        f'<span>Tabulka: <code>{html.escape(gs["table"])}</code></span></div>'
        f'<div class="srow schema"><span>Schéma: {schema_html}</span></div>'
        '<div class="srow schema"><span><code>type</code> nabývá: '
        '<code>undecided</code> <i>nevíme (default, nezrevidováno)</i> · '
        '<code>no-bolt</code> <i>potvrzeno že borhák chybí</i> · '
        '<code>bolt</code> <i>potvrzený borhák (s cx/cy/radius_px)</i></span></div>'
        '<div class="srow schema"><span><code>radius_src</code> nabývá: '
        '<code>default</code> <i>nezměřeno</i> · '
        '<code>sam</code> <i>změřeno SAMem</i> · '
        '<code>manual</code> <i>ručně doladěno slidery</i></span></div>'
        '<div class="srow schema"><span><code>osm_source</code> formát: '
        'JSON pole 1:n vazeb na OSM prvky, např. '
        '<code>[{"osmId": 123, "osmType": "node", "key": "wikimedia_commons:path", "order": 1}, …]</code>'
        '</span></div>'
        '</div>'
    )

    doc = f"""<!DOCTYPE html><html lang="cs"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Výřezy borháků – strana {page}/{pages}</title>
<style>
  :root {{ --red:#ff2222; --bg:#111; --fg:#eee; --panel:#1c1c1c; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
    font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
  header {{ position:static; background:var(--panel);
    border-bottom:1px solid #333; padding:10px 14px; display:flex; gap:12px;
    align-items:center; flex-wrap:wrap; }}
  header h1 {{ font-size:15px; margin:0; flex:1 1 auto; }}
  header a.home {{ color:#7bf; text-decoration:none; font-size:14px; }}
  #status {{ font-size:13px; color:#aaa; }}
  .pager {{ display:flex; gap:12px; align-items:center; justify-content:center;
    flex-wrap:wrap; padding:14px; color:#aaa; font-size:13px; }}
  .btn {{ background:#2a2a2a; color:var(--fg); border:1px solid #444;
    padding:7px 13px; border-radius:6px; font-size:14px; cursor:pointer;
    text-decoration:none; }}
  .btn.disabled {{ opacity:.35; pointer-events:none; }}
  .jump {{ display:flex; gap:6px; }}
  .jump input {{ width:70px; background:#2a2a2a; color:var(--fg);
    border:1px solid #444; border-radius:6px; padding:6px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
    gap:12px; padding:14px; max-width:1300px; margin:0 auto; }}
  .mobile-only {{ display:none; }}
  @media (max-width:700px) {{
    .mobile-only {{ display:inline-block; }}
    .grid.onecol {{ grid-template-columns:1fr; max-width:none; gap:18px; }}
    .grid.onecol .cap {{ font-size:14px; }}
    /* mobil: zelený odznak 2× větší a dobře klikací (žádný dwell) */
    .cell.t-bolt .badge-yes {{ width:60px; height:60px; font-size:34px;
      top:8px; left:8px; opacity:1 !important; }}
    /* mobil: slidery 2× větší (track, jezdec i popisky) */
    .sliders {{ gap:8px; }}
    .sliders label {{ font-size:20px; gap:12px; }}
    .sliders input[type=range] {{ height:30px; -webkit-appearance:none;
      appearance:none; background:transparent; }}
    .sliders input[type=range]::-webkit-slider-runnable-track {{
      height:8px; background:#444; border-radius:4px; }}
    .sliders input[type=range]::-webkit-slider-thumb {{
      -webkit-appearance:none; appearance:none; width:32px; height:32px;
      margin-top:-12px; border-radius:50%; background:#3d3; }}
    .sliders input[type=range]::-moz-range-track {{
      height:8px; background:#444; border-radius:4px; }}
    .sliders input[type=range]::-moz-range-thumb {{
      width:32px; height:32px; border:none; border-radius:50%; background:#3d3; }}
  }}
  .cell {{ margin:0; background:var(--panel); border:1px solid #333;
    border-radius:8px; padding:8px; user-select:none; }}
  .imgwrap {{ position:relative; line-height:0;
    border:2px solid transparent; border-radius:5px; overflow:hidden;
    transition:.12s; }}
  @media (hover:hover) {{ .imgwrap:hover {{ border-color:#000; }} }}
  .cell img {{ width:100%; height:auto; display:block; background:#000; }}
  /* SVG kružnice nad náhledem (jen pro potvrzené bolty) */
  .ovl {{ position:absolute; inset:0; width:100%; height:100%;
    pointer-events:none; display:none; }}
  .ovl circle {{ fill:none; stroke:#3d3; stroke-width:2.5;
    vector-effect:non-scaling-stroke; }}
  /* levá/pravá klikací zóna */
  .zone {{ position:absolute; top:0; bottom:0; width:50%; cursor:default; z-index:2; }}
  .zone-left {{ left:0; }}
  .zone-right {{ right:0; }}
  .badge {{ position:absolute; top:6px; width:30px; height:30px;
    border-radius:50%; color:#fff; display:none; z-index:3;
    align-items:center; justify-content:center; font-weight:700; font-size:18px;
    box-shadow:0 1px 4px rgba(0,0,0,.6); pointer-events:none; }}
  .badge-no {{ right:6px; background:var(--red); }}
  .badge-yes {{ left:6px; background:#22a52e; }}
  /* hover: napověz obě ikonky poloprůhledně – JEN na zařízeních s hover (desktop) */
  @media (hover:hover) {{ .imgwrap:hover .badge {{ display:flex; opacity:.45; }} }}
  .cap {{ display:flex; flex-direction:column; font-size:11px; color:#bbb;
    margin-top:6px; line-height:1.35; word-break:break-word; text-decoration:none; }}
  .cap span {{ color:#000; }}
  .cap:hover b {{ color:#7bf; text-decoration:underline; }}
  /* slidery – jen u potvrzených boltů */
  .sliders {{ display:none; flex-direction:column; gap:2px; margin-top:6px; }}
  .sliders label {{ display:flex; align-items:center; gap:6px; font-size:11px;
    color:#9c9; }}
  .sliders input[type=range] {{ flex:1; accent-color:#3d3; height:14px; }}
  /* ── no-bolt: červená dlaždice, čárkované ohraničení, BEZ grayscale ── */
  .cell.t-nobolt {{ background:rgba(255,34,34,.30); border-color:#a03030; }}
  .cell.t-nobolt .imgwrap {{ border:2px dashed var(--red); }}
  .cell.t-nobolt .badge-no {{ display:flex; opacity:1; }}
  /* ── potvrzený bolt: zelená dlaždice, zelené čárkování, kružnice + slidery ── */
  .cell.t-bolt {{ background:rgba(40,200,60,.22); border-color:#2e7a3a; }}
  .cell.t-bolt .imgwrap {{ border:2px dashed #3d3; }}
  .cell.t-bolt .badge-yes {{ display:flex; opacity:1; }}
  .cell.t-bolt .ovl {{ display:block; }}
  .cell.t-bolt .sliders {{ display:flex; }}
  /* potvrzený bolt: kružnice táhnutelná myší, zóny vypnuté, odznaky klikací */
  .cell.t-bolt .ovl {{ pointer-events:auto; cursor:default; z-index:4; }}
  .cell.t-bolt .ovl.dragging {{ cursor:default; }}
  .cell.t-bolt .zone {{ pointer-events:none; }}
  /* odznaky NAD táhnutelnou kružnicí (z-index 4), ať klik nezačne drag */
  .cell.t-bolt .badge {{ pointer-events:auto; cursor:default; z-index:6; }}
  @media (hover:hover) {{ .cell.t-bolt .badge:hover {{ opacity:1 !important; transform:scale(1.12); }} }}
  /* hover-dwell: podržení 500 ms nad zeleným odznakem = klik (yes→nevíme) */
  .badge-yes.dwelling {{ animation:dwellpulse .2s linear forwards; }}
  @keyframes dwellpulse {{
    from {{ box-shadow:0 0 0 0 rgba(34,165,46,.85); }}
    to   {{ box-shadow:0 0 0 10px rgba(34,165,46,0); }}
  }}
  .cell.busy {{ opacity:.4; }}
  /* skryté filtrem show= (zůstává v DOM → pevné stránkování) */
  .cell.filtered-out {{ display:none; }}
  /* zvýraznění cílové buňky přes #bNNN anchor */
  .cell:target {{ box-shadow:0 0 0 4px #ff0, 0 0 24px rgba(255,220,0,.55);
    animation:tgt-flash .8s ease-out; }}
  @keyframes tgt-flash {{
    from {{ box-shadow:0 0 0 6px #ff0, 0 0 40px rgba(255,220,0,.9); }}
    to   {{ box-shadow:0 0 0 4px #ff0, 0 0 24px rgba(255,220,0,.55); }} }}
  #regen {{ display:flex; align-items:center; gap:10px; padding:10px 14px;
    background:#161616; border-bottom:1px solid #333; font-size:13px;
    color:#aaa; flex-wrap:wrap; }}
  #regen.hidden {{ display:none; }}
  #regenbar {{ flex:1 1 240px; height:14px; background:#2a2a2a;
    border-radius:7px; overflow:hidden; min-width:160px; }}
  #regenfill {{ height:100%; width:0%; background:linear-gradient(90deg,#3a7,#5c9);
    transition:width .3s; }}
  #regenbtn {{ background:#7a2a2a; }}
  #regenbtn:hover {{ background:#9a3030; }}
  .stats {{ margin:14px; padding:12px 16px; background:#161616;
    border:1px solid #333; border-radius:8px; font-size:13px; color:#bbb; }}
  .stats .srow {{ display:flex; flex-wrap:wrap; gap:6px 22px; margin:3px 0; }}
  .stats b {{ color:#eee; }}
  .stats code {{ background:#222; padding:1px 5px; border-radius:4px; color:#9cf; }}
  .stats .schema i {{ color:#8a8; font-style:normal; }}
</style></head><body>
<header>
  <h1>Výřezy borháků – ◧ vlevo potvrdit bolt · ◨ vpravo no-bolt</h1>
  <form method="get" action="/crops" style="display:flex;gap:6px;align-items:center">
    <label style="font-size:13px;color:#aaa;white-space:nowrap">Nenalezené z:</label>
    <select name="filter" onchange="window.location='/crops?filter='+encodeURIComponent(this.value)+'{sqs}'" style="background:#2a2a2a;color:#eee;border:1px solid #555;padding:5px 8px;border-radius:6px;font-size:13px;max-width:260px">
      <option value="">— vše —</option>
      {filter_opts}
    </select>
  </form>
  <button class="btn" id="markallbtn" title="Označit všechny zobrazené cropy jako no-bolt" onclick="(async()=>{{const cells=[...document.querySelectorAll('.cell:not(.t-nobolt)')];if(!cells.length){{alert('Všechny cropy na stránce jsou už no-bolt.');return;}}if(!confirm('Označit '+cells.length+' cropů jako no-bolt?'))return;const batch=cells.map(c=>JSON.parse(c.dataset.meta));this.disabled=true;try{{const res=await fetch('/api/mark',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(batch)}});await res.json();cells.forEach(c=>{{c.classList.remove('t-undecided','t-bolt');c.classList.add('t-nobolt');}});}}catch(e){{console.log('Chyba: '+e);}}finally{{this.disabled=false;}}}})()">✕ Označit vše jako no-bolt</button>
  <button class="btn mobile-only" id="onecol-btn">⬍ 1 sloupec</button>
  <button class="btn" id="sizebtn" title="Přepnout velikost náhledu">{'⊟ Normální náhled' if size == 'huge' else ('⊞ 4× větší náhled' if size == 'big' else '⊞ 2× větší náhled')}</button>
  <button class="btn" id="regenbtn">↻ Purge &amp; recreate crops cache</button>
  <a class="btn" href="/debug">🔍 YOLO debug</a>
  <a class="btn" href="/test-dataset">🧪 Test dataset</a>
  <a class="btn" style="background:{('#2a2a4a' if show=='undecided' else '#2a2a2a')}" href="/crops?show=undecided{sqs}">? Nevíme</a>
  <a class="btn" style="background:{('#3a1a1a' if show=='no-bolt' else '#2a2a2a')}" href="/crops?show=no-bolt{sqs}">✗ No-bolt</a>
  <a class="btn" style="background:{('#1a3a1a' if show=='bolt' else '#2a2a2a')}" href="/crops?show=bolt{sqs}">✓ Potvrzené bolty</a>
  <a class="btn" style="background:{('#333' if not show else '#2a2a2a')}" href="/crops{(f'?size={size}' if size in ('big','huge') else '')}">⬚ Vše</a>
  <a class="home" href="/view">← přehledový prohlížeč</a>
</header>
<div id="regen" class="hidden">
  <div id="regenbar"><div id="regenfill"></div></div>
  <span id="regentext">—</span>
</div>
{pager()}
<div class="grid">{''.join(cells)}</div>
{pager()}
{stats_html}
<script>
// ── velikost náhledu: přesměruj podle uložené volby ──────────────────────────
(function() {{
  const params = new URLSearchParams(location.search);
  const saved = localStorage.getItem('crops_size');
  if (!params.has('size') && (saved === 'big' || saved === 'huge')) {{
    params.set('size', saved);
    location.replace(location.pathname + '?' + params.toString());
  }}
}})();
document.addEventListener('DOMContentLoaded', () => {{
const SC = {CROP_SCALE};    // px náhledu na 1 orig px
const OUTV = {outv};        // rozměr SVG viewBoxu / náhledu (px)
const OUTH = OUTV / 2;      // střed viewBoxu
const CLAMPV = {clampv};    // max posun středu od klik. bodu (orig px)
const canHover = window.matchMedia('(hover: hover)').matches;  // desktop vs dotyk

// ── mobil: přepínač 1 sloupec / mřížka (uloženo v localStorage) ──────────────
const grid = document.querySelector('.grid');
const onecolBtn = document.getElementById('onecol-btn');
function applyOnecol(on) {{
  grid.classList.toggle('onecol', on);
  onecolBtn.textContent = on ? '▦ Mřížka' : '⬍ 1 sloupec';
}}
applyOnecol(localStorage.getItem('crops_onecol') === '1');
onecolBtn.addEventListener('click', () => {{
  const on = !grid.classList.contains('onecol');
  localStorage.setItem('crops_onecol', on ? '1' : '0');
  applyOnecol(on);
}});

// ── přepínač velikosti náhledu (normal → big → huge → normal) ────────────────
const sizeBtn = document.getElementById('sizebtn');
const CUR_SIZE = '{size}';
sizeBtn.addEventListener('click', () => {{
  const next = CUR_SIZE === 'normal' ? 'big' : (CUR_SIZE === 'big' ? 'huge' : 'normal');
  localStorage.setItem('crops_size', next);
  const params = new URLSearchParams(location.search);
  if (next === 'normal') params.delete('size'); else params.set('size', next);
  location.href = location.pathname + '?' + params.toString();
}});

function setType(cell, t) {{
  cell.classList.remove('t-undecided','t-nobolt','t-bolt');
  cell.classList.add(t === 'no-bolt' ? 't-nobolt'
                   : t === 'bolt'    ? 't-bolt' : 't-undecided');
}}

async function mark(cell, action) {{
  if (cell.classList.contains('busy')) return;
  const meta = JSON.parse(cell.dataset.meta);
  cell.classList.add('busy');
  try {{
    const res = await fetch('/api/mark', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify(Object.assign({{}}, meta, {{action}}))
    }});
    const j = await res.json();
    setType(cell, j.type);
    if (j.type === 'bolt') {{ drawCircle(cell); saveGeom(cell, true); }}
  }} catch(e) {{ console.log('Chyba: ' + e); }}
  finally {{ cell.classList.remove('busy'); }}
}}

function sliders(cell) {{
  return {{
    r:  parseFloat(cell.querySelector('.s-r').value),
    dx: parseFloat(cell.querySelector('.s-x').value),
    dy: parseFloat(cell.querySelector('.s-y').value),
  }};
}}

function drawCircle(cell) {{
  const {{r, dx, dy}} = sliders(cell);
  const circ = cell.querySelector('.ovl circle');
  circ.setAttribute('cx', OUTH + dx * SC);
  circ.setAttribute('cy', OUTH + dy * SC);
  circ.setAttribute('r',  r * SC);
}}

const saveTimers = new WeakMap();
function saveGeom(cell, immediate) {{
  const meta = JSON.parse(cell.dataset.meta);
  const x = parseFloat(cell.dataset.x), y = parseFloat(cell.dataset.y);
  const W = parseFloat(cell.dataset.w), H = parseFloat(cell.dataset.h);
  const {{r, dx, dy}} = sliders(cell);
  const cx = x + (W ? dx / W : 0);
  const cy = y + (H ? dy / H : 0);
  const doSave = () => fetch('/api/crop-geom', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify(Object.assign({{}}, meta, {{cx, cy, radius_px: r}}))
    }}).catch(e => console.log('save geom: ' + e));
  clearTimeout(saveTimers.get(cell));
  if (immediate) doSave();
  else saveTimers.set(cell, setTimeout(doSave, 250));
}}

// posun středu z pozice ukazatele → nastav slidery x/y (orig px, clamp ±CLAMPV)
function placeFromEvent(cell, e) {{
  const wrap = cell.querySelector('.imgwrap');
  const rect = wrap.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const vbx = (e.clientX - rect.left) / rect.width * OUTV;
  const vby = (e.clientY - rect.top) / rect.height * OUTV;
  const clamp = v => Math.max(-CLAMPV, Math.min(CLAMPV, v));
  cell.querySelector('.s-x').value = clamp((vbx - OUTH) / SC).toFixed(2);
  cell.querySelector('.s-y').value = clamp((vby - OUTH) / SC).toFixed(2);
  drawCircle(cell);
}}

document.querySelectorAll('.cell').forEach(cell => {{
  cell.querySelector('.zone-left').addEventListener('click', () => mark(cell, 'bolt'));
  cell.querySelector('.zone-right').addEventListener('click', () => mark(cell, 'no-bolt'));
  cell.querySelectorAll('.sliders input').forEach(inp => {{
    inp.addEventListener('input', () => {{ drawCircle(cell); saveGeom(cell, false); }});
  }});

  // u potvrzeného boltu jsou odznaky klikací (zóny jsou vypnuté)
  const byes = cell.querySelector('.badge-yes');
  const bno = cell.querySelector('.badge-no');
  byes.addEventListener('click', () => mark(cell, 'bolt'));
  bno.addEventListener('click', () => mark(cell, 'no-bolt'));

  // hover-dwell na zeleném odznaku = klik – JEN na desktopu (hover zařízení).
  // Na dotyku se NEpřipojuje vůbec, aby tap fungoval na první dotek (žádný
  // sticky-hover / dvojklik).
  if (canHover) {{
    let dwell = null;
    byes.addEventListener('pointerenter', (e) => {{
      if (e.pointerType && e.pointerType !== 'mouse') return;
      if (!cell.classList.contains('t-bolt')) return;
      byes.classList.add('dwelling');
      dwell = setTimeout(() => {{ byes.classList.remove('dwelling'); mark(cell, 'bolt'); }}, 200);
    }});
    const cancelDwell = () => {{ clearTimeout(dwell); dwell = null; byes.classList.remove('dwelling'); }};
    byes.addEventListener('pointerleave', cancelDwell);
    byes.addEventListener('pointerdown', cancelDwell);
  }}

  // drag & drop zelené kružnice (jen myš / desktop; mobil používá slidery)
  const ovl = cell.querySelector('.ovl');
  let dragging = false;
  ovl.addEventListener('pointerdown', (e) => {{
    if (e.pointerType !== 'mouse') return;          // mobil → slidery
    if (!cell.classList.contains('t-bolt')) return;
    e.preventDefault();
    dragging = true;
    ovl.classList.add('dragging');
    ovl.setPointerCapture(e.pointerId);
    placeFromEvent(cell, e);                          // klik = umísti hned
  }});
  ovl.addEventListener('pointermove', (e) => {{
    if (!dragging) return;
    placeFromEvent(cell, e);
    saveGeom(cell, false);
  }});
  const endDrag = (e) => {{
    if (!dragging) return;
    dragging = false;
    ovl.classList.remove('dragging');
    saveGeom(cell, true);
  }};
  ovl.addEventListener('pointerup', endDrag);
  ovl.addEventListener('pointercancel', endDrag);

  // mobil: tap do obrázku nastaví střed (jen u potvrzeného boltu).
  // Scroll se nepřeruší – když prst ujede, tap se ignoruje.
  if (!canHover) {{
    let tStart = null;
    ovl.addEventListener('pointerdown', (e) => {{
      if (e.pointerType === 'mouse') return;
      if (!cell.classList.contains('t-bolt')) return;
      tStart = {{ x: e.clientX, y: e.clientY }};
    }});
    ovl.addEventListener('pointerup', (e) => {{
      if (e.pointerType === 'mouse' || !tStart) return;
      const moved = Math.hypot(e.clientX - tStart.x, e.clientY - tStart.y);
      tStart = null;
      if (moved < 12 && cell.classList.contains('t-bolt')) {{
        placeFromEvent(cell, e);     // nastav střed na místo tapnutí
        saveGeom(cell, true);
      }}
    }});
  }}
}});

// ---- purge & recreate crops cache ----
const regenBox  = document.getElementById('regen');
const regenFill = document.getElementById('regenfill');
const regenText = document.getElementById('regentext');
const regenBtn  = document.getElementById('regenbtn');
let regenTimer = null;

function fmtDur(s) {{
  if (s == null) return '?';
  s = Math.round(s);
  const m = Math.floor(s / 60), sec = s % 60;
  return m > 0 ? `${{m}}m ${{sec}}s` : `${{sec}}s`;
}}

function renderProgress(p) {{
  regenBox.classList.remove('hidden');
  regenFill.style.width = (p.pct || 0).toFixed(1) + '%';
  if (p.running) {{
    const rate = p.rate ? p.rate.toFixed(1) : '?';
    regenText.textContent =
      `${{p.done}}/${{p.total}} (${{(p.pct||0).toFixed(1)}} %) · `
      + `${{rate}} img/s · ETA ${{fmtDur(p.eta)}}`
      + (p.errors ? ` · ${{p.errors}} chyb` : '');
    regenBtn.disabled = true;
  }} else {{
    const tail = p.error ? `CHYBA: ${{p.error}}`
      : `hotovo: ${{p.done}}/${{p.total}}` + (p.errors ? ` (${{p.errors}} chyb)` : '');
    regenText.textContent = tail;
    regenBtn.disabled = false;
    if (regenTimer) {{ clearInterval(regenTimer); regenTimer = null; }}
  }}
}}

async function pollProgress() {{
  try {{
    const r = await fetch('/api/cache-progress');
    renderProgress(await r.json());
  }} catch (e) {{ /* ignoruj jednorázový výpadek */ }}
}}

regenBtn.addEventListener('click', async () => {{
  if (!confirm('Smazat celou keš výřezů a vygenerovat znovu? '
      + 'Běží sekvenčně (max 2 paralelně), může to chvíli trvat.')) return;
  regenBtn.disabled = true;
  try {{
    const r = await fetch('/api/purge-cache', {{ method: 'POST' }});
    const j = await r.json();
    if (!j.started && r.status === 409) {{
      alert('Regenerace už běží.');
    }}
    renderProgress(j);
    if (!regenTimer) regenTimer = setInterval(pollProgress, 1000);
  }} catch (e) {{
    alert('Chyba při spuštění: ' + e);
    regenBtn.disabled = false;
  }}
}});

// při načtení stránky zjisti, jestli něco neběží (a navaž polling)
pollProgress().then(() => {{
  fetch('/api/cache-progress').then(r => r.json()).then(p => {{
    if (p.running && !regenTimer) regenTimer = setInterval(pollProgress, 1000);
    else if (!p.running && (p.done === 0 && p.total === 0)) regenBox.classList.add('hidden');
  }});
}});
}}); // DOMContentLoaded
</script>
</body></html>"""
    return doc.encode("utf-8")


import csv as _csv
import glob as _glob


def _list_result_files() -> list[str]:
    files = []
    for ext in ("csv", "txt"):
        files += _glob.glob(os.path.join(HERE, f"test_dataset_results_*.{ext}"))
    return sorted(os.path.basename(p) for p in files)


def _load_result_file(filename: str, half: int) -> tuple[list, str]:
    """Returns ([(crop_dict, result), ...], model_name) for the given half size."""
    safe = os.path.basename(filename)
    if not safe.startswith("test_dataset_results_"):
        return [], ""
    filepath = os.path.join(HERE, safe)
    if not os.path.isfile(filepath):
        return [], ""

    crop_lookup = {(c["file"], c["pos"]): c for c in CROPS}
    rows = []
    model_name = ""

    if safe.endswith(".txt"):
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if int(row["half"]) != half:
                    continue
                key = (row["file"], row["pos"])
                c = crop_lookup.get(key) or {
                    "file": row["file"], "x": float(row["x"]),
                    "y": float(row["y"]), "pos": row["pos"],
                    "order": row.get("order", 0), "osmId": row.get("osmId", 0),
                    "osmType": row.get("osmType", ""), "key": row.get("key", ""),
                    "image": row.get("image", ""),
                }
                rows.append((c, row["result"]))
                model_name = row.get("model", "")
    else:
        with open(filepath, encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                if int(row["half"]) != half:
                    continue
                key = (row["file"], row["pos"])
                c = crop_lookup.get(key) or {
                    "file": row["file"], "x": float(row["x"]),
                    "y": float(row["y"]), "pos": row["pos"],
                    "order": 0, "osmId": 0, "osmType": "", "key": "", "image": "",
                }
                rows.append((c, row["result"]))
                model_name = row.get("model", "")
    return rows, model_name


def render_test_dataset(results_file: str = "", half: int = 80) -> bytes:
    available = _list_result_files()
    result_rows, model_name = [], ""
    if results_file:
        result_rows, model_name = _load_result_file(results_file, half)

    def make_cell(c: dict, result: str | None) -> str:
        img_url = "/crop-img?" + urllib.parse.urlencode(
            {"file": c["file"], "x": c["x"], "y": c["y"], "half": half})
        view_url = "/view?" + urllib.parse.urlencode(
            {"file": c["file"], "bx": c["x"], "by": c["y"]})
        label = f'{html.escape(c["file"])} · #{c["order"]} · {html.escape(c["pos"])}'

        badge = ""
        if result == "Y":
            badge = '<span class="res res-y">ANO</span>'
        elif result == "N":
            badge = '<span class="res res-n">NE</span>'
        elif result == "?":
            badge = '<span class="res res-q">?</span>'

        return (
            f'<div class="tcell">'
            f'<a href="{html.escape(view_url)}" class="twrap">'
            f'<img loading="lazy" src="{img_url}" alt="">'
            f'{badge}'
            f'</a>'
            f'<a class="tcap" href="{html.escape(view_url)}">{label}</a>'
            f'</div>'
        )

    # Build sections
    if result_rows:
        groups: dict[str, list] = {"Y": [], "N": [], "?": []}
        for c, res in result_rows:
            groups[res].append(make_cell(c, res))
        sections_html = ""
        labels = {"Y": ("✓ ANO – bolt viditelný", "#3a7"), "N": ("✗ NE – bolt neviditelný", "#c44"), "?": ("? NEVÍM", "#777")}
        for key in ("Y", "N", "?"):
            cells = groups[key]
            if not cells:
                continue
            title, col = labels[key]
            sections_html += (
                f'<h2 class="section-head" style="color:{col}">'
                f'{title} <span class="cnt">({len(cells)})</span></h2>'
                f'<div class="grid">{"".join(cells)}</div>'
            )
        body_content = sections_html
    else:
        cells = [make_cell(c, None) for c in TEST_DATASET]
        body_content = f'<div class="grid">{"".join(cells)}</div>'

    # Selectbox options
    sel_options = '<option value="">— bez výsledků —</option>'
    for fname in available:
        sel = ' selected' if fname == results_file else ''
        sel_options += f'<option value="{html.escape(fname)}"{sel}>{html.escape(fname)}</option>'

    half_20 = ' selected' if half == 20 else ''
    half_80 = ' selected' if half == 80 else ''

    info = f'model: {html.escape(model_name)}' if model_name else ''

    doc = f"""<!DOCTYPE html><html lang="cs"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test dataset – {len(TEST_DATASET)} cropů</title>
<style>
  :root {{ --bg:#111; --fg:#eee; --panel:#1c1c1c; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
    font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
  header {{ position:sticky; top:0; z-index:5; background:var(--panel);
    border-bottom:1px solid #333; padding:10px 14px; display:flex; gap:10px;
    align-items:center; flex-wrap:wrap; }}
  header h1 {{ font-size:15px; margin:0; flex:1 1 auto; }}
  .btn {{ background:#2a2a2a; color:var(--fg); border:1px solid #444;
    padding:6px 12px; border-radius:6px; font-size:13px; cursor:pointer;
    text-decoration:none; }}
  select {{ background:#2a2a2a; color:var(--fg); border:1px solid #555;
    padding:6px 10px; border-radius:6px; font-size:13px; cursor:pointer; }}
  .info {{ font-size:12px; color:#000; }}
  .section-head {{ margin:16px 16px 4px; font-size:14px; }}
  .cnt {{ font-weight:normal; color:#000; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
    gap:12px; padding:8px 16px; max-width:1400px; }}
  .tcell {{ background:var(--panel); border:1px solid #333; border-radius:8px;
    padding:8px; }}
  .twrap {{ position:relative; display:block; line-height:0; border-radius:5px;
    overflow:hidden; }}
  .twrap img {{ width:100%; aspect-ratio:1; object-fit:cover; display:block; }}
  .twrap:hover img {{ opacity:.85; }}
  .res {{ position:absolute; top:6px; right:6px; font-size:12px; font-weight:700;
    padding:2px 7px; border-radius:4px; color:#fff; pointer-events:none; }}
  .res-y {{ background:#3a7; }}
  .res-n {{ background:#c44; }}
  .res-q {{ background:#666; }}
  .tcap {{ display:block; font-size:10px; color:#000; margin-top:5px;
    word-break:break-word; text-decoration:none; line-height:1.3; }}
  .tcap:hover {{ color:#7bf; }}
</style></head><body>
<header>
  <h1>Test dataset – {len(TEST_DATASET)} cropů</h1>
  <form method="get" action="/test-dataset" style="display:flex;gap:8px;align-items:center">
    <select name="results" onchange="this.form.submit()">
      {sel_options}
    </select>
    <select name="half" onchange="this.form.submit()">
      <option value="20"{half_20}>±20 px</option>
      <option value="80"{half_80}>±80 px</option>
    </select>
    <span class="info">{info}</span>
  </form>
  <a class="btn" href="/test_dataset.txt">⬇ dataset</a>
  <a class="btn" href="/crops">← /crops</a>
</header>
{body_content}
</body></html>"""
    return doc.encode("utf-8")


# ----------------------------------------------------------------- HTTP handler

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/html; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _static(self, relpath, ctype):
        path = os.path.join(HERE, relpath)
        if not os.path.isfile(path):
            self._send(404, b"not found", "text/plain")
            return
        with open(path, "rb") as f:
            data = f.read()
        self._send(200, data, ctype)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        path = u.path

        if path == "/":
            # aplikace startuje na stránce s výřezy
            self._send(302, b"", "text/html", {"Location": "/crops"})
        elif path in ("/view", "/index.html"):
            self._static("index.html", "text/html; charset=utf-8")
        elif path == "/data.json":
            self._static("data.json", "application/json; charset=utf-8")
        elif path == "/crops.json":
            self._static("crops.json", "application/json; charset=utf-8")
        elif path == "/crops":
            try:
                page = int(qs.get("page", ["1"])[0])
            except ValueError:
                page = 1
            filter_results = qs.get("filter", [""])[0]
            show = qs.get("show", [""])[0]
            size = qs.get("size", [""])[0]
            self._send(200, render_crops(page, filter_results, show, size))
        elif path == "/crops-precise-571":
            self._send(200, render_precise_page())
        elif path.startswith("/crops-precise-571/"):
            name = urllib.parse.unquote(path[len("/crops-precise-571/"):])
            if not name or "/" in name or ".." in name:
                self._send(400, b"bad file", "text/plain"); return
            fp = os.path.join(PRECISE_DIR, name)
            if not os.path.isfile(fp):
                self._send(404, b"not found", "text/plain"); return
            with open(fp, "rb") as f:
                data = f.read()
            self._send(200, data, "image/jpeg",
                       {"Cache-Control": "public, max-age=3600"})
        elif path == "/crops-precise-full":
            self._send(200, render_precise_full_page())
        elif path.startswith("/crops-precise-full/"):
            name = urllib.parse.unquote(path[len("/crops-precise-full/"):])
            if not name or "/" in name or ".." in name:
                self._send(400, b"bad file", "text/plain"); return
            fp = os.path.join(PRECISE_FULL_DIR, name)
            if not os.path.isfile(fp):
                self._send(404, b"not found", "text/plain"); return
            with open(fp, "rb") as f:
                data = f.read()
            self._send(200, data, "image/jpeg",
                       {"Cache-Control": "public, max-age=3600"})
        elif path == "/debug":
            self._send(200, render_debug())
        elif path == "/api/photo-bolts":
            fname = qs.get("file", [""])[0]
            if not fname or "/" in fname or ".." in fname:
                self._send(400, b"bad file", "text/plain"); return
            data = json.dumps(get_photo_bolts(fname)).encode("utf-8")
            self._send(200, data, "application/json")
        elif path == "/api/detections":
            fname = qs.get("file", [""])[0]
            if not fname or "/" in fname or ".." in fname:
                self._send(400, b"bad file", "text/plain"); return
            data = json.dumps({"boxes": get_detections(fname)}).encode("utf-8")
            self._send(200, data, "application/json")
        elif path == "/model/best.onnx":
            fp = os.path.join(MODELS_DIR, "best.onnx")
            if not os.path.isfile(fp):
                self._send(404, b"model not found", "text/plain"); return
            with open(fp, "rb") as f:
                data = f.read()
            self._send(200, data, "application/octet-stream",
                       {"Cache-Control": "public, max-age=3600"})
        elif path == "/test-dataset":
            rf = qs.get("results", [""])[0]
            try:
                th = int(qs.get("half", ["80"])[0])
            except ValueError:
                th = 80
            self._send(200, render_test_dataset(rf, th))
        elif path == "/test_dataset.txt":
            self._static("test_dataset.txt", "text/plain; charset=utf-8")
        elif path.startswith("/test_dataset_results_") and (path.endswith(".csv") or path.endswith(".txt")):
            name = os.path.basename(path)
            ctype = "text/csv; charset=utf-8" if name.endswith(".csv") else "text/plain; charset=utf-8"
            self._static(name, ctype)
        elif path == "/api/cache-progress":
            self._send(200, json.dumps(regen_progress()).encode(),
                       "application/json")
        elif path == "/static":
            name = qs.get("file", [""])[0]
            if not name or "/" in name or ".." in name:
                self._send(400, b"bad file", "text/plain"); return
            fp = os.path.join(HERE, "static", name)
            if not os.path.isfile(fp):
                self._send(404, b"not found", "text/plain"); return
            ext = os.path.splitext(name)[1].lower()
            ctype = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".svg": "image/svg+xml",
            }.get(ext, "application/octet-stream")
            with open(fp, "rb") as f:
                data = f.read()
            self._send(200, data, ctype)
        elif path == "/crop-img":
            try:
                fname = qs["file"][0]
                x = float(qs["x"][0]); y = float(qs["y"][0])
                half = int(qs["half"][0]) if "half" in qs else HALF
                half = max(10, min(200, half))
                out = int(qs["out"][0]) if "out" in qs else OUT
                out = max(20, min(800, out))
                if "/" in fname or ".." in fname:
                    raise ValueError("bad name")
                data = make_crop(fname, x, y, half, out)
                self._send(200, data, "image/jpeg",
                           {"Cache-Control": "public, max-age=86400"})
            except Exception as e:
                self._send(404, f"crop error: {e}".encode(), "text/plain")
        elif path.startswith("/images/"):
            name = urllib.parse.unquote(path[len("/images/"):])
            if "/" in name or ".." in name:
                self._send(403, b"forbidden", "text/plain"); return
            fp = os.path.join(IMG_DIR, name)
            if not os.path.isfile(fp):
                self._send(404, b"not found", "text/plain"); return
            with open(fp, "rb") as f:
                data = f.read()
            self._send(200, data, "image/jpeg",
                       {"Cache-Control": "public, max-age=86400"})
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/purge-cache":
            qs = urllib.parse.parse_qs(u.query)
            try:
                workers = int(qs.get("workers", ["2"])[0])
            except ValueError:
                workers = 2
            workers = max(1, min(workers, 2))   # 1 vCPU → max 2
            started = start_regen(workers)
            self._send(200 if started else 409,
                       json.dumps({"started": started,
                                   **regen_progress()}).encode(),
                       "application/json")
            return
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length else b"{}"

        def upsert_type(cur, item, new_type):
            """Zajisti řádek v crop_labels (klíč image+position) a nastav type."""
            image = item.get("image"); position = item["position"]
            row = cur.execute(
                "SELECT type FROM crop_labels WHERE image=? AND position=?",
                (image, position)).fetchone()
            if row is None:
                src = json.dumps(item.get("sources", []), ensure_ascii=False)
                cur.execute(
                    "INSERT INTO crop_labels "
                    "(image, position, type, osm_source) VALUES (?,?,?,?)",
                    (image, position, new_type, src))
            else:
                cur.execute(
                    "UPDATE crop_labels SET type=?, updated_at=datetime('now') "
                    "WHERE image=? AND position=?",
                    (new_type, image, position))

        # ── geometrie potvrzeného boltu (slidery: cx, cy, radius) ──────────────
        if u.path == "/api/crop-geom":
            try:
                p = json.loads(raw_body or b"{}")
                image = p.get("image"); position = p["position"]
                conn = db(); cur = conn.cursor()
                cur.execute(
                    "UPDATE crop_labels SET cx=?, cy=?, radius_px=?, "
                    "radius_src='manual', type='bolt', updated_at=datetime('now') "
                    "WHERE image=? AND position=?",
                    (float(p["cx"]), float(p["cy"]), float(p["radius_px"]),
                     image, position))
                if cur.rowcount == 0:
                    upsert_type(cur, p, "bolt")
                    cur.execute(
                        "UPDATE crop_labels SET cx=?, cy=?, radius_px=?, "
                        "radius_src='manual' WHERE image=? AND position=?",
                        (float(p["cx"]), float(p["cy"]), float(p["radius_px"]),
                         image, position))
                conn.commit(); conn.close()
                self._send(200, json.dumps({"ok": True}).encode(), "application/json")
            except Exception as e:
                self._send(400, json.dumps({"error": str(e)}).encode(),
                           "application/json")
            return

        if u.path != "/api/mark":
            self._send(404, b"not found", "text/plain"); return
        try:
            payload = json.loads(raw_body or b"{}")

            # batch: array of items → označit vše jako no-bolt, vrať {marked: N}
            if isinstance(payload, list):
                conn = db(); cur = conn.cursor(); count = 0
                for item in payload:
                    upsert_type(cur, item, "no-bolt"); count += 1
                conn.commit(); conn.close()
                self._send(200, json.dumps({"marked": count}).encode(),
                           "application/json")
                return

            # single item → toggle: action ('bolt'|'no-bolt') ⇄ 'undecided'
            action = payload.get("action", "no-bolt")
            if action not in ("bolt", "no-bolt"):
                action = "no-bolt"
            image = payload.get("image"); position = payload["position"]
            conn = db(); cur = conn.cursor()
            row = cur.execute(
                "SELECT type FROM crop_labels WHERE image=? AND position=?",
                (image, position)).fetchone()
            new_type = "undecided" if (row and row["type"] == action) else action
            upsert_type(cur, payload, new_type)
            conn.commit(); conn.close()
            self._send(200, json.dumps({"type": new_type}).encode(),
                       "application/json")
        except Exception as e:
            self._send(400, json.dumps({"error": str(e)}).encode(),
                       "application/json")


class Server(ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server(("0.0.0.0", PORT), Handler) as httpd:
        print(f"Bolt Viewer běží na http://0.0.0.0:{PORT}  "
              f"({TOTAL} výřezů, {PAGES} stran)", flush=True)
        httpd.serve_forever()
