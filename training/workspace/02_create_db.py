#!/usr/bin/env python3
"""
Step 02: Builds climbing_paths.sqlite with tables:
  - climbing_paths  (raw OSM data: osmId, osmType, image, path)
  - crop_labels     (bolt points, deduplicated, ready for annotation)

Only ever creates these two tables (+ sqlite_sequence). Archive-only tables
such as crop_labels_predupe / crops_with_no_bolts (found in the hand-labeled
training/climbing_paths.sqlite kept in the repo as a reference archive) are
never produced here and never copied by --seed — that archive is not to be
modified or reproduced, only read from.

Replaces the original gen_crops.py + migrate_crop_labels.py without the
intermediate crops.json step.

crop_labels holds one row per physical bolt, deduplicated by (image, position).
The same bolt may be referenced by several OSM elements (a 1:n relationship);
those references are kept as a JSON array in the `osm_source` column, e.g.
  [{"osmId": 123, "osmType": "node", "key": "wikimedia_commons:path", "order": 1}, ...]
rather than as scalar columns, so no reference is lost to dedup.

The OSM export is always read implicitly from data/openclimbing_latest.sqlite
(the symlink step 01 produces) — override with --osm-db if needed.

Usage:
  # Fresh build from the OSM export, all bolts start as undecided:
  python3 02_create_db.py

  # Seed + update: keep existing labels from an already-labeled archive DB
  # untouched, add any bolts newly present in the OSM export, and refresh
  # climbing_paths from that export:
  python3 02_create_db.py --seed ../climbing_paths.sqlite
"""
import argparse
import json
import os
import re
import sqlite3

SUFFIX_RE = re.compile(r"^([\d.]+),([\d.]+)([A-Za-z])$")

SCHEMA_SQL = """
    DROP TABLE IF EXISTS climbing_paths;
    CREATE TABLE climbing_paths (
        osmId   INTEGER NOT NULL,
        osmType TEXT    NOT NULL,
        image   TEXT    NOT NULL,
        path    TEXT    NOT NULL
    );

    DROP TABLE IF EXISTS crop_labels;
    CREATE TABLE crop_labels (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        image       TEXT    NOT NULL,
        position    TEXT    NOT NULL,
        cx          REAL    NOT NULL,
        cy          REAL    NOT NULL,
        type        TEXT    NOT NULL DEFAULT 'undecided',
        radius_px   REAL,
        radius_src  TEXT    NOT NULL DEFAULT 'default',
        osm_source  TEXT,
        updated_at  TEXT    DEFAULT (datetime('now')),
        UNIQUE(image, position)
    );
"""


def extract_from_osm(osm_db_path: str):
    """Extract (climbing_paths rows, bolts) from an OSM export.

    bolts: (image, position) -> {"cx", "cy", "sources": [ {osmId, osmType, key, order} ]}
    """
    src = sqlite3.connect(osm_db_path)
    path_rows = []
    bolts: dict[tuple, dict] = {}

    for osmId, osmType, tags_json in src.execute(
        "SELECT osmId, osmType, tags FROM climbing_features "
        "WHERE tags LIKE '%wikimedia_commons%path%'"
    ):
        tags = json.loads(tags_json or "{}")
        for key, path_val in tags.items():
            if not key.endswith(":path"):
                continue
            image = tags.get(key[:-5])
            if not image:
                continue

            path_rows.append((osmId, osmType, image, path_val))

            order = 0
            for point in path_val.split("|"):
                m = SUFFIX_RE.match(point)
                if not m:
                    continue
                cx, cy, suffix = float(m[1]), float(m[2]), m[3]
                if suffix != "B":
                    continue
                order += 1
                src_ref = {"osmId": osmId, "osmType": osmType,
                           "key": key, "order": order}
                k = (image, point)
                if k in bolts:
                    bolts[k]["sources"].append(src_ref)
                else:
                    bolts[k] = {"cx": cx, "cy": cy, "sources": [src_ref]}

    src.close()
    return path_rows, bolts


def seed_and_update(seed_path: str, osm_db_path: str, out_path: str) -> None:
    """Seed crop_labels from an already-labeled archive DB, then add any
    bolts newly present in a fresher OSM export that aren't in the archive
    yet. Existing labeled rows are copied verbatim — labels are never
    touched. climbing_paths is always rebuilt fresh from the OSM export,
    since it's derived/raw data, not something anyone labels by hand.

    crop_labels.id is AUTOINCREMENT and the labeling UI paginates by that
    id order, so page contents must stay stable as the DB grows: seed rows
    are (re-)inserted first, in their original id order, so they keep the
    same low ids; new bolts are appended strictly after, sorted by
    (image, position) for a readable grouping within that new tail — never
    interleaved into the middle of already-paginated pages.
    """
    path_rows, bolts = extract_from_osm(osm_db_path)

    seed = sqlite3.connect(seed_path)
    dst = sqlite3.connect(out_path)
    dst.executescript(SCHEMA_SQL)

    for row in path_rows:
        dst.execute("INSERT INTO climbing_paths VALUES (?,?,?,?)", row)

    existing_keys = set()
    n_seeded = 0
    for row in seed.execute(
        "SELECT image, position, cx, cy, type, radius_px, radius_src, "
        "osm_source, updated_at FROM crop_labels ORDER BY id"
    ):
        dst.execute(
            "INSERT INTO crop_labels (image, position, cx, cy, type, "
            "radius_px, radius_src, osm_source, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)", row)
        existing_keys.add((row[0], row[1]))
        n_seeded += 1

    n_new = 0
    for image, position in sorted(bolts):
        if (image, position) in existing_keys:
            continue
        b = bolts[(image, position)]
        dst.execute(
            "INSERT INTO crop_labels (image, position, cx, cy, osm_source) "
            "VALUES (?,?,?,?,?)",
            (image, position, b["cx"], b["cy"],
             json.dumps(b["sources"], ensure_ascii=False)),
        )
        n_new += 1

    dst.commit()
    seed.close()
    dst.close()

    print(f"Seeded from archive: {seed_path}")
    print(f"Updated from OSM export: {osm_db_path}")
    print(f"climbing_paths: {len(path_rows)} rows (fresh from export)")
    print(f"crop_labels:    {n_seeded} kept from archive (labels untouched) "
          f"+ {n_new} new bolts = {n_seeded + n_new} total")
    print("(crop_labels_predupe / crops_with_no_bolts intentionally not copied "
          "— archive-only tables.)")
    print(f"Saved: {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--osm-db", default="data/openclimbing_latest.sqlite",
                     help="path to the OSM export (default: "
                          "data/openclimbing_latest.sqlite, the symlink "
                          "step 01 produces)")
    ap.add_argument("--out", default="data/climbing_paths.sqlite",
                    help="output DB (default: data/climbing_paths.sqlite)")
    ap.add_argument("--seed",
                     help="seed crop_labels from an already-labeled archive "
                          "DB, keeping its existing labels untouched, add "
                          "any bolts newly present in --osm-db, and refresh "
                          "climbing_paths from --osm-db")
    args = ap.parse_args()

    # Never clobber an existing DB — it may hold manual labels. Delete it by
    # hand if you really want to rebuild from scratch.
    if os.path.exists(args.out):
        print(f"{args.out} already exists — nothing to do "
              f"(delete it to rebuild from scratch).")
        return

    if not os.path.exists(args.osm_db):
        ap.error(f"OSM export not found: {args.osm_db} "
                 f"(run 01_download_osm.sh first, or pass --osm-db)")

    if args.seed:
        seed_and_update(args.seed, args.osm_db, args.out)
        return

    path_rows, bolts = extract_from_osm(args.osm_db)

    dst = sqlite3.connect(args.out)
    dst.executescript(SCHEMA_SQL)

    for row in path_rows:
        dst.execute("INSERT INTO climbing_paths VALUES (?,?,?,?)", row)

    for image, position in sorted(bolts):
        b = bolts[(image, position)]
        dst.execute(
            "INSERT INTO crop_labels (image, position, cx, cy, osm_source) "
            "VALUES (?,?,?,?,?)",
            (image, position, b["cx"], b["cy"],
             json.dumps(b["sources"], ensure_ascii=False)),
        )

    dst.commit()
    dst.close()

    n_refs = sum(len(b["sources"]) for b in bolts.values())
    print(f"climbing_paths: {len(path_rows)} rows")
    print(f"crop_labels:    {len(bolts)} bolts (deduped by image+position, "
          f"type=undecided)")
    print(f"  osm references: {n_refs} ({n_refs - len(bolts)} folded into "
          f"osm_source as 1:n)")
    print(f"Saved: {args.out}")


if __name__ == "__main__":
    main()
