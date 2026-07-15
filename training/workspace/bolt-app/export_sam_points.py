#!/usr/bin/env python3
"""
Fáze 3 příprava: export bodů pro SAM (s identifikátory pro zpětný import).

Výstup: bolt-points/points_sam.json = [
  {"file","osmId","osmType","key","position","cx","cy"}, ...
]
Jen bolt-kandidáti (type != 'no-bolt'). SAM jim změří poloměr.
"""
import json
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "..", "climbing_paths.sqlite")
CROPS_JSON = os.path.join(HERE, "crops.json")
OUT = os.path.join(HERE, "bolt-points", "points_sam.json")


def main():
    crops = json.load(open(CROPS_JSON, encoding="utf-8"))
    key2crop = {(c["osmId"], c["key"], c["pos"]): c for c in crops}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT osm_id, key, position, cx, cy FROM crop_labels "
        "WHERE type != 'no-bolt'"
    ).fetchall()
    conn.close()

    out = []
    missing = 0
    for r in rows:
        c = key2crop.get((r["osm_id"], r["key"], r["position"]))
        if not c:
            missing += 1
            continue
        out.append({
            "file": c["file"], "osmId": r["osm_id"], "osmType": c["osmType"],
            "key": r["key"], "position": r["position"],
            "cx": r["cx"], "cy": r["cy"],
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"bodů pro SAM: {len(out)}  (přeskočeno {missing})")
    print(f"zapsáno: {OUT}  ({os.path.getsize(OUT)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
