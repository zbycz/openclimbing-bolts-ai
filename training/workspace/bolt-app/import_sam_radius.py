#!/usr/bin/env python3
"""Import SAM poloměrů do crop_labels. NEPŘEPISUJE ruční anotace (radius_src='manual')."""
import csv, os, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "..", "climbing_paths.sqlite")
CSV = "/tmp/sam-out/sam_radius.csv"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# SAM hodnoty podle (osm_id,key,position)
sam = {}
for r in csv.DictReader(open(CSV, encoding="utf-8")):
    try:
        sam[(int(r["osmId"]), r["key"], r["position"])] = float(r["radius_px"])
    except (ValueError, KeyError):
        pass

updated = skipped_manual = skipped_norad = 0
for (osm_id, key, pos), rad in sam.items():
    row = cur.execute("SELECT radius_src FROM crop_labels "
                      "WHERE osm_id=? AND key=? AND position=?",
                      (osm_id, key, pos)).fetchone()
    if row is None:
        continue
    if row["radius_src"] == "manual":
        skipped_manual += 1
        continue
    cur.execute("UPDATE crop_labels SET radius_px=?, radius_src='sam', "
                "updated_at=datetime('now') WHERE osm_id=? AND key=? AND position=?",
                (round(rad, 2), osm_id, key, pos))
    updated += 1

# řádky s prázdným radiusem (SAM fail) zůstávají default
conn.commit()

print(f"SAM hodnot v CSV: {len(sam)}")
print(f"  aktualizováno (radius_src=sam): {updated}")
print(f"  zachováno ruční (manual): {skipped_manual}")

# přehled
print("\n=== distribuce radius_src ===")
for r in cur.execute("SELECT radius_src, COUNT(*) n FROM crop_labels GROUP BY radius_src"):
    print(f"  {r['radius_src']:8} {r['n']}")

# porovnání manual vs SAM
print("\n=== MANUAL vs SAM (tvoje ruční anotace) ===")
for r in cur.execute("SELECT osm_id,key,position,radius_px FROM crop_labels "
                     "WHERE radius_src='manual'"):
    s = sam.get((r["osm_id"], r["key"], r["position"]))
    diff = f"{abs(r['radius_px']-s):.1f}px" if s is not None else "—"
    print(f"  {r['position']:14}  ruční={r['radius_px']:.1f}px  "
          f"SAM={s if s is None else f'{s:.1f}px'}  Δ={diff}")
conn.close()
