#!/usr/bin/env python3
"""
Generate data.json for the bolt viewer.

Reads ../climbing_paths.sqlite, finds every image (with bolts) that was
actually downloaded into ./images, and aggregates all bolt (B) positions
across every climbing path that references that image.

Output: data.json = [ { "file": "<filename.jpg>", "image": "File:...",
                        "bolts": [[x,y], ...] }, ... ]
where x,y are fractions (0..1) of the image width/height.
"""

import hashlib
import json
import os
import re
import sqlite3
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "..", "climbing_paths.sqlite")
IMG_DIR = os.path.join(HERE, "images")
OUT = os.path.join(HERE, "data.json")

# Matches a coordinate that is a bolt: "0.542,0.427B"
BOLT_RE = re.compile(r"(\d*\.?\d+),(\d*\.?\d+)B")


def commons_filename(file_tag: str) -> str:
    """Same transform as getCommonsImageUrl.ts -> local filename."""
    name = file_tag.removeprefix("File:")
    name = urllib.parse.unquote(name).replace(" ", "_")
    return name


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT image, path FROM climbing_paths WHERE path LIKE '%B%' ORDER BY image"
    )

    # image tag -> ordered, de-duplicated list of bolt coords
    by_image: dict[str, list] = {}
    for image, path in cur.fetchall():
        if not path:
            continue
        for m in BOLT_RE.finditer(path):
            x, y = float(m.group(1)), float(m.group(2))
            by_image.setdefault(image, [])
            pt = [round(x, 4), round(y, 4)]
            if pt not in by_image[image]:
                by_image[image].append(pt)
    conn.close()

    result = []
    missing = 0
    for image in sorted(by_image.keys()):
        fname = commons_filename(image)
        if not os.path.exists(os.path.join(IMG_DIR, fname)):
            missing += 1
            continue
        if not by_image[image]:
            continue
        result.append({"file": fname, "image": image, "bolts": by_image[image]})

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=0)

    total_bolts = sum(len(r["bolts"]) for r in result)
    print(f"Obrázků zapsáno: {len(result)}")
    print(f"Borháků celkem:  {total_bolts}")
    print(f"Chybějící soubory (vynecháno): {missing}")


if __name__ == "__main__":
    main()
