#!/usr/bin/env python3
"""
Fáze 1 migrace: crops_with_no_bolts  →  crop_labels (per-crop stav + poloměr).

Vytvoří novou tabulku crop_labels a naplní ji ze všech CROPS:
  - type = 'has-bolt' (default), nebo 'no-bolt' pokud je crop v crops_with_no_bolts
  - cx, cy = normalizovaný střed (z crops.json)
  - radius_px = NULL (doplní později SAM), radius_src = 'default'

Stará tabulka crops_with_no_bolts zůstává nedotčená jako záloha.
Idempotentní: pokud crop_labels už existuje a obsahuje ruční úpravy
(radius_src != 'default' nebo type='undecided'), migrace se NEPŘEPÍŠE.
"""
import json
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "..", "climbing_paths.sqlite")
CROPS_JSON = os.path.join(HERE, "crops.json")

DDL = """
CREATE TABLE IF NOT EXISTS crop_labels (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  image       TEXT,
  osm_id      INTEGER,
  osm_type    TEXT,
  key         TEXT,
  position    TEXT,
  bolt_order  INTEGER,
  cx          REAL,
  cy          REAL,
  type        TEXT NOT NULL DEFAULT 'has-bolt',
  radius_px   REAL,
  radius_src  TEXT NOT NULL DEFAULT 'default',
  updated_at  TEXT DEFAULT (datetime('now')),
  UNIQUE(osm_id, key, position)
);
"""


def main():
    with open(CROPS_JSON, encoding="utf-8") as f:
        crops = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # bezpečnostní pojistka: nepřepiš ruční práci
    existing = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='crop_labels'"
    ).fetchone()
    if existing:
        manual = cur.execute(
            "SELECT COUNT(*) FROM crop_labels "
            "WHERE radius_src != 'default' OR type = 'undecided'"
        ).fetchone()[0]
        if manual:
            print(f"crop_labels už existuje a má {manual} ručních úprav — KONČÍM "
                  f"(spusť s vymazáním ručně, pokud opravdu chceš přepsat).")
            conn.close()
            return
        print("crop_labels existuje bez ručních úprav — přegeneruju.")
        cur.execute("DROP TABLE crop_labels")

    cur.executescript(DDL)

    # množina no-bolt klíčů (osm_id, key, position)
    no_bolt = {
        (r["osm_id"], r["key"], r["position"])
        for r in cur.execute(
            "SELECT osm_id, key, position FROM crops_with_no_bolts")
    }
    print(f"no-bolt v původní tabulce: {len(no_bolt)}")

    inserted = 0
    nb_matched = 0
    for c in crops:
        k = (c["osmId"], c["key"], c["pos"])
        typ = "no-bolt" if k in no_bolt else "has-bolt"
        if typ == "no-bolt":
            nb_matched += 1
        cur.execute(
            "INSERT OR IGNORE INTO crop_labels "
            "(image, osm_id, osm_type, key, position, bolt_order, cx, cy, "
            " type, radius_px, radius_src) "
            "VALUES (?,?,?,?,?,?,?,?,?,NULL,'default')",
            (c["image"], c["osmId"], c["osmType"], c["key"], c["pos"],
             c["order"], c["x"], c["y"], typ),
        )
        inserted += cur.rowcount

    conn.commit()

    total = cur.execute("SELECT COUNT(*) FROM crop_labels").fetchone()[0]
    has = cur.execute(
        "SELECT COUNT(*) FROM crop_labels WHERE type='has-bolt'").fetchone()[0]
    nb = cur.execute(
        "SELECT COUNT(*) FROM crop_labels WHERE type='no-bolt'").fetchone()[0]
    conn.close()

    print(f"vloženo: {inserted}  (z {len(crops)} crops)")
    print(f"no-bolt napárováno: {nb_matched} / {len(no_bolt)}")
    print(f"crop_labels celkem: {total}  has-bolt={has}  no-bolt={nb}")
    orphans = len(no_bolt) - nb_matched
    if orphans:
        print(f"POZN: {orphans} no-bolt záznamů nemá protějšek v crops.json "
              f"(crop možná zmizel z datasetu) — ponechány jen ve staré tabulce.")


if __name__ == "__main__":
    main()
