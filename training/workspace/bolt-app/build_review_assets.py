#!/usr/bin/env python3
"""
Vyrobí dvě věci pro ruční review:

1) test_dataset_results_conflicts48.txt
   = 48 cropů, kde P4-shiny řekl Y, ale jsou v no_bolt DB.
   Objeví se v <select> na /test-dataset.

2) static/random20_p4shiny.html
   = 20 náhodných P4-shiny=Y cropů, na každém vykreslené boxy ve DVOU režimech:
     A) ABSOLUTNĚ — 40/60/100 px v originále (stejně velké na každé fotce)
     B) RELATIVNĚ — 1.5/2.5/4 % šířky DANÉ fotky (na každé fotce jiný počet px)
   Servíruje se přes /static?file=random20_p4shiny.html
"""
import json, os, random, sqlite3, urllib.parse
from PIL import Image

HERE   = os.path.dirname(os.path.abspath(__file__))
IMGDIR = os.path.join(HERE, "images")
SHINY  = os.path.join(HERE, "test_dataset_results_p4shiny_all.txt")
HALF   = 80                # crop region = ±80 px → 160 px
DISP   = 240               # px na obrazovce (crop 160 px originálu → DISP)
SCALE  = DISP / (2 * HALF) # px originálu → px na obrazovce

lines = [json.loads(l) for l in open(SHINY, encoding="utf-8") if l.strip()]
Y = [o for o in lines if o["result"] == "Y"]

# ── 1) 48 konfliktů (Y, ale v no_bolt DB) ─────────────────────────────────────
conn = sqlite3.connect(os.path.join(HERE, "..", "climbing_paths.sqlite"))
conn.row_factory = sqlite3.Row
db_keys = {(int(r["osm_id"]), r["position"])
           for r in conn.execute("SELECT osm_id, position FROM crops_with_no_bolts")}
conn.close()

conflicts = [o for o in Y if (int(o["osmId"]), o["pos"]) in db_keys]
out_conf = os.path.join(HERE, "test_dataset_results_conflicts48.txt")
with open(out_conf, "w", encoding="utf-8") as f:
    for o in conflicts:
        rec = dict(o); rec["half"] = HALF
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print(f"Konfliktů zapsáno: {len(conflicts)} → {os.path.basename(out_conf)}")

# ── 2) 20 náhodných Y ─────────────────────────────────────────────────────────
random.seed(20)
sample = random.sample(Y, 20)

_dim_cache = {}
def photo_w(fname):
    if fname not in _dim_cache:
        _dim_cache[fname] = Image.open(os.path.join(IMGDIR, fname)).size  # (W,H)
    return _dim_cache[fname][0]

def crop_url(o):
    return "/crop-img?" + urllib.parse.urlencode(
        {"file": o["file"], "x": o["x"], "y": o["y"], "half": HALF})

def overlay(disp_px, color, label):
    return (f'<div class="box" style="width:{disp_px:.0f}px;height:{disp_px:.0f}px;'
            f'border-color:{color}"><span class="bl" style="background:{color}">'
            f'{label}</span></div>')

def cell(o, boxes_html):
    return (f'<figure><div class="wrap" style="width:{DISP}px;height:{DISP}px">'
            f'<img src="{crop_url(o)}" width="{DISP}" height="{DISP}">{boxes_html}'
            f'</div><figcaption>{o["file"]}<br>{o["pos"]}</figcaption></figure>')

# Sekce A — absolutní px (40/60/100 px originálu → *SCALE px na obrazovce)
cells_abs = []
for o in sample:
    boxes = (overlay(100 * SCALE, "#ff5555", "100px")
             + overlay(60 * SCALE, "#55ff55", "60px")
             + overlay(40 * SCALE, "#ffdd33", "40px"))
    cells_abs.append(cell(o, boxes))

# Sekce B — relativní % šířky dané fotky
cells_rel = []
for o in sample:
    W = photo_w(o["file"])
    parts = []
    for frac, color in ((0.04, "#ff5555"), (0.025, "#55ff55"), (0.015, "#ffdd33")):
        opx = frac * W                       # px v originále
        parts.append(overlay(opx * SCALE, color, f"{frac*100:.1f}%·{opx:.0f}px"))
    cells_rel.append(cell(o, "".join(parts)))

html = f"""<!doctype html><meta charset="utf-8">
<title>20 náhodných P4-shiny=ANO — volba velikosti boxu</title>
<style>
  body{{background:#111;color:#ddd;font-family:system-ui,sans-serif;margin:20px}}
  h1{{font-size:19px}} h2{{font-size:16px;margin-top:34px;border-top:1px solid #333;padding-top:18px}}
  .legend{{margin:6px 0 16px;font-size:13px;color:#bbb}}
  .legend b{{padding:1px 6px;border-radius:4px;color:#000}}
  .grid{{display:flex;flex-wrap:wrap;gap:18px}}
  figure{{margin:0}}
  .wrap{{position:relative;outline:1px solid #333;overflow:hidden}}
  .wrap img{{display:block}}
  .box{{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
        border:2px solid;box-sizing:border-box;pointer-events:none}}
  .bl{{position:absolute;top:-1px;left:-1px;font-size:9px;color:#000;
       padding:0 3px;line-height:13px;border-radius:0 0 4px 0;white-space:nowrap}}
  figcaption{{font-size:11px;color:#888;width:{DISP}px;margin-top:4px;word-break:break-word}}
</style>
<h1>20 náhodných borháků (P4-shiny = ANO) — která velikost boxu sedí?</h1>

<h2>A) Absolutně — pevné px v originále (stejné na každé fotce)</h2>
<div class="legend">
  <b style="background:#ff5555">100px</b>
  <b style="background:#55ff55">60px</b>
  <b style="background:#ffdd33">40px</b>
  &nbsp;— box má vždy stejný počet px bez ohledu na rozlišení fotky.
  Na vysokém rozlišení proto pokrývá menší kus skály než na nízkém.</div>
<div class="grid">{''.join(cells_abs)}</div>

<h2>B) Relativně — % šířky DANÉ fotky (na každé fotce jiný počet px)</h2>
<div class="legend">
  <b style="background:#ff5555">4 %</b>
  <b style="background:#55ff55">2.5 %</b>
  <b style="background:#ffdd33">1.5 %</b>
  &nbsp;— box je podíl šířky fotky; popisek ukazuje i kolik px to na té fotce vyjde.
  Všimni si, jak se px hodnota mění foto od fota (velká fotka → velký box).</div>
<div class="grid">{''.join(cells_rel)}</div>
"""

os.makedirs(os.path.join(HERE, "static"), exist_ok=True)
out_html = os.path.join(HERE, "static", "random20_p4shiny.html")
with open(out_html, "w", encoding="utf-8") as f:
    f.write(html)
print(f"Stránka: static/{os.path.basename(out_html)}  (20 cropů, 2 sekce)")
