#!/usr/bin/env python3
"""
Generate crops.json – plochý seznam VŠECH výřezů borháků (B).

Čte z původní openclimbing sqlite (kvůli klíči wikimedia_commons:*:path),
filtruje na obrázky, které máme reálně stažené v ./images, a pro každý
path-string vyjmenuje borháky (body končící na 'B') v původním pořadí.

Každý záznam = jeden výřez:
  {
    "file":    "Roviště_-_Dračí_stěna2.jpg",   # lokální soubor v ./images
    "image":   "File:Roviště - Dračí stěna2.jpg",
    "key":     "wikimedia_commons:path",        # který klíč path patří
    "osmId":   7395990034,
    "osmType": "node",
    "order":   1,                                # pořadí boltu v rámci path (1-based)
    "pos":     "0.542,0.427B",                   # přesný token, jak byl ve stringu
    "x": 0.542, "y": 0.427
  }
"""

import hashlib
import json
import os
import re
import sqlite3
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DB = os.path.join(HERE, "..", "openclimbing_2026-06-27_154937.sqlite")
IMG_DIR = os.path.join(HERE, "images")
OUT = os.path.join(HERE, "crops.json")

# Jeden bod cesty: "0.542,0.427B" / "0.447,0.158A" / "0.371,0.57"
POINT_RE = re.compile(r"^(\d*\.?\d+),(\d*\.?\d+)([A-Z]?)$")


def commons_filename(file_tag: str) -> str:
    name = file_tag.removeprefix("File:")
    return urllib.parse.unquote(name).replace(" ", "_")


def main():
    conn = sqlite3.connect(SRC_DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT osmId, osmType, tags FROM climbing_features "
        "WHERE tags LIKE '%wikimedia_commons%path%'"
    )

    crops = []
    for osmId, osmType, tj in cur.fetchall():
        if not tj:
            continue
        tags = json.loads(tj)
        for key, path in tags.items():
            if not key.endswith(":path") or not path:
                continue
            image = tags.get(key[:-5])  # odřízni ":path"
            if not image:
                continue
            fname = commons_filename(image)
            if not os.path.exists(os.path.join(IMG_DIR, fname)):
                continue
            order = 0
            for token in path.split("|"):
                m = POINT_RE.match(token.strip())
                if not m:
                    continue
                x, y, typ = m.group(1), m.group(2), m.group(3)
                if typ != "B":          # jen borháky
                    continue
                order += 1
                crops.append({
                    "file": fname,
                    "image": image,
                    "key": key,
                    "osmId": osmId,
                    "osmType": osmType,
                    "order": order,
                    "pos": token.strip(),
                    "x": round(float(x), 4),
                    "y": round(float(y), 4),
                })
    conn.close()

    # stabilní řazení: dle obrázku, pak klíče, pak pořadí
    crops.sort(key=lambda c: (c["file"].lower(), c["key"], c["osmId"], c["order"]))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(crops, f, ensure_ascii=False)

    print(f"Výřezů (borháků) celkem: {len(crops)}")
    print(f"Unikátních obrázků:      {len({c['file'] for c in crops})}")


if __name__ == "__main__":
    main()
