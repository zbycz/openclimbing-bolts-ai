#!/usr/bin/env python3
"""
Deduplikace crop_labels: 1 řádek = 1 fyzický bolt = (image, position).
Původ (více OSM prvků) se uloží do JSON pole osm_source.

Záloha: crop_labels → crop_labels_predupe (přejmenování staré tabulky).
"""
import json
import os
import sqlite3
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "..", "climbing_paths.sqlite")

SRC_RANK = {"manual": 3, "sam": 2, "default": 1}


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT image, position, osm_id, osm_type, key, bolt_order, "
        "type, cx, cy, radius_px, radius_src, updated_at FROM crop_labels"
    ).fetchall()

    groups = defaultdict(list)
    for r in rows:
        groups[(r["image"], r["position"])].append(r)

    print(f"vstup: {len(rows)} řádků → {len(groups)} unikátních (image, position)")

    # přejmenuj starou tabulku na zálohu (smaž případnou předchozí zálohu)
    cur.execute("DROP TABLE IF EXISTS crop_labels_predupe")
    cur.execute("ALTER TABLE crop_labels RENAME TO crop_labels_predupe")

    cur.execute("""
        CREATE TABLE crop_labels (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          image       TEXT,
          position    TEXT,
          cx          REAL,
          cy          REAL,
          type        TEXT NOT NULL DEFAULT 'undecided',
          radius_px   REAL,
          radius_src  TEXT NOT NULL DEFAULT 'default',
          osm_source  TEXT,
          updated_at  TEXT DEFAULT (datetime('now')),
          UNIQUE(image, position)
        )
    """)

    def chosen_type(g):
        # manuálně potvrzený bolt > explicitní no-bolt > auto bolt > undecided
        if any(r["type"] == "bolt" and r["radius_src"] == "manual" for r in g):
            return "bolt"
        if any(r["type"] == "no-bolt" for r in g):
            return "no-bolt"
        if any(r["type"] == "bolt" for r in g):
            return "bolt"
        return "undecided"

    def geom_rank(r):
        return (SRC_RANK.get(r["radius_src"], 0), r["updated_at"] or "")

    n_ins = 0
    for (image, position), g in groups.items():
        rep = max(g, key=geom_rank)               # geometrie z nejlepšího zdroje
        typ = chosen_type(g)
        # osm_source: deduplikované zdroje, seřazené dle order
        seen = set(); sources = []
        for r in sorted(g, key=lambda x: (x["bolt_order"] or 0)):
            k = (r["osm_id"], r["key"], r["bolt_order"])
            if k in seen:
                continue
            seen.add(k)
            sources.append({
                "osmId": r["osm_id"], "osmType": r["osm_type"],
                "key": r["key"], "order": r["bolt_order"],
            })
        cur.execute(
            "INSERT INTO crop_labels "
            "(image, position, cx, cy, type, radius_px, radius_src, osm_source, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (image, position, rep["cx"], rep["cy"], typ,
             rep["radius_px"], rep["radius_src"],
             json.dumps(sources, ensure_ascii=False), rep["updated_at"]),
        )
        n_ins += 1

    conn.commit()

    print(f"vloženo: {n_ins} unikátních boltů")
    print("=== distribuce type ===")
    for r in cur.execute("SELECT type, COUNT(*) n FROM crop_labels GROUP BY type"):
        print(f"  {r['type']:10} {r['n']}")
    multi = cur.execute(
        "SELECT COUNT(*) FROM crop_labels WHERE json_array_length(osm_source) > 1"
    ).fetchone()[0]
    print(f"boltů s víc OSM zdroji: {multi}")
    print("integrity:", cur.execute("PRAGMA integrity_check").fetchone()[0])
    print("záloha: crop_labels_predupe")
    conn.close()


if __name__ == "__main__":
    main()
