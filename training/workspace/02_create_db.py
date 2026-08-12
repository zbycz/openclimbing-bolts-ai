#!/usr/bin/env python3
"""
Step 02: Builds climbing_paths.sqlite with tables:
  - climbing_paths  (raw OSM data: osmId, osmType, image, path)
  - crop_labels     (protection points, deduplicated, ready for annotation)

Only ever creates these two tables (+ sqlite_sequence). Archive-only tables
such as crop_labels_predupe / crops_with_no_bolts (found in the hand-labeled
training/climbing_paths.sqlite kept in the repo as a reference archive) are
never produced here and never copied by --seed — that archive is not to be
modified or reproduced, only read from.

Replaces the original gen_crops.py + migrate_crop_labels.py without the
intermediate crops.json step.

crop_labels holds one row per physical protection point, deduplicated by
(image, position). The same point may be referenced by several OSM elements
(a 1:n relationship); those references are kept as a JSON array in the
`osm_source` column, e.g.
  [{"osmId": 123, "osmType": "node", "key": "wikimedia_commons:path", "order": 1}, ...]
rather than as scalar columns, so no reference is lost to dedup.

Path points carry a one-letter suffix saying what is at that spot. All fixed
protection is imported (PROTECTION_SUFFIXES) — it is all hardware bolted or
wedged into the rock, which is what the detector has to find:

  B  bolt / ring    progress protection, secures the climber during ascent
  A  anchor/abseil  usually several bolts on a chain, for rappel/descent
  P  piton          traditional, hammered into a crack
  S  fixed sling    sling anchor left in place for protection

  U  unfinished     NOT imported (SKIPPED_SUFFIXES) — this is not a piece of
                    protection at all, it only marks that the drawn route is
                    incomplete (e.g. it runs off the edge of the photo).

Suffixless points are plain route-line vertices and are ignored, as before.
The suffix stays visible in `position` (its last character), so a row's origin
is always recoverable, e.g.  SELECT substr(position,-1) ...

The OSM export is always read implicitly from data/openclimbing_latest.sqlite
(the symlink step 01 produces) — override with --osm-db if needed.

Running this repeatedly is the normal way to pick up new OSM data: an existing
--out DB is updated in place, never rebuilt. Points that are already there keep
their id and their label; only points not yet present are appended.

Usage:
  # Fresh build, or incremental update of an existing DB — same command:
  python3 02_create_db.py

  # Seed + update: build a NEW DB from an already-labeled archive DB, keeping
  # its existing labels untouched, add any points newly present in the OSM
  # export, and refresh climbing_paths from that export:
  python3 02_create_db.py --seed ../climbing_paths.sqlite --out data/new.sqlite
"""
import argparse
import json
import os
import re
import sqlite3

SUFFIX_RE = re.compile(r"^([\d.]+),([\d.]+)([A-Za-z])$")

# Fixed protection — imported as crop_labels rows. See the module docstring.
PROTECTION_SUFFIXES = {"B", "A", "P", "S"}
# Not protection, only a marker that the route drawing is incomplete. Counted
# in the report so the number stays visible, never imported.
SKIPPED_SUFFIXES = {"U"}

PATHS_SCHEMA_SQL = """
    DROP TABLE IF EXISTS climbing_paths;
    CREATE TABLE climbing_paths (
        osmId   INTEGER NOT NULL,
        osmType TEXT    NOT NULL,
        image   TEXT    NOT NULL,
        path    TEXT    NOT NULL
    );
"""

LABELS_SCHEMA_SQL = """
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

SCHEMA_SQL = PATHS_SCHEMA_SQL + LABELS_SCHEMA_SQL


def extract_from_osm(osm_db_path: str):
    """Extract (climbing_paths rows, points, skipped) from an OSM export.

    points:  (image, position) -> {"cx", "cy", "suffix",
                                   "sources": [ {osmId, osmType, key, order} ]}
    skipped: suffix -> {"raw": int, "keys": set of (image, position)} for the
             suffixes deliberately not imported (SKIPPED_SUFFIXES)
    """
    src = sqlite3.connect(osm_db_path)
    path_rows = []
    points: dict[tuple, dict] = {}
    skipped: dict[str, dict] = {s: {"raw": 0, "keys": set()}
                                for s in SKIPPED_SUFFIXES}

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
                if suffix in SKIPPED_SUFFIXES:
                    skipped[suffix]["raw"] += 1
                    skipped[suffix]["keys"].add((image, point))
                    continue
                if suffix not in PROTECTION_SUFFIXES:
                    continue
                order += 1
                src_ref = {"osmId": osmId, "osmType": osmType,
                           "key": key, "order": order}
                k = (image, point)
                if k in points:
                    points[k]["sources"].append(src_ref)
                else:
                    points[k] = {"cx": cx, "cy": cy, "suffix": suffix,
                                 "sources": [src_ref]}

    src.close()
    return path_rows, points, skipped


def report_skipped(skipped: dict) -> None:
    for suffix, info in sorted(skipped.items()):
        print(f"  suffix {suffix}: {len(info['keys'])} unique "
              f"({info['raw']} raw) not imported — not protection")


def suffix_breakdown(points: dict) -> str:
    counts: dict[str, int] = {}
    for p in points.values():
        counts[p["suffix"]] = counts.get(p["suffix"], 0) + 1
    return ", ".join(f"{s}={counts[s]}" for s in sorted(counts))


def update_in_place(out_path: str, osm_db_path: str) -> None:
    """Update an existing DB from a fresher OSM export.

    crop_labels rows already present are left completely alone — same id, same
    type, same radius — because they may carry hand-made labels. Only points
    the DB has never seen are appended, sorted by (image, position) within that
    new tail. Since existing ids never move, the labeling UI's pagination stays
    stable and already-reviewed pages keep their contents (see seed_and_update
    for the same reasoning).

    Nothing is ever deleted: a point that disappeared from OSM keeps its row and
    its label rather than silently dropping reviewed work.

    climbing_paths is derived/raw data that nobody labels by hand, so it is
    rebuilt from the export every time.
    """
    path_rows, points, skipped = extract_from_osm(osm_db_path)

    dst = sqlite3.connect(out_path)
    dst.executescript(PATHS_SCHEMA_SQL)
    for row in path_rows:
        dst.execute("INSERT INTO climbing_paths VALUES (?,?,?,?)", row)

    existing_keys = {(image, position) for image, position in
                     dst.execute("SELECT image, position FROM crop_labels")}
    n_before = len(existing_keys)

    new_keys = [k for k in sorted(points) if k not in existing_keys]
    for image, position in new_keys:
        p = points[(image, position)]
        dst.execute(
            "INSERT INTO crop_labels (image, position, cx, cy, osm_source) "
            "VALUES (?,?,?,?,?)",
            (image, position, p["cx"], p["cy"],
             json.dumps(p["sources"], ensure_ascii=False)),
        )

    dst.commit()
    n_total = dst.execute("SELECT COUNT(*) FROM crop_labels").fetchone()[0]
    gone = len(existing_keys - set(points))
    dst.close()

    new_by_suffix = suffix_breakdown({k: points[k] for k in new_keys})
    print(f"Updated in place: {out_path}")
    print(f"OSM export: {osm_db_path}")
    print(f"climbing_paths: {len(path_rows)} rows (rebuilt from export)")
    print(f"crop_labels:    {n_before} kept (labels untouched) "
          f"+ {len(new_keys)} new = {n_total} total")
    if new_keys:
        print(f"  new by suffix: {new_by_suffix}")
    print(f"  in export: {len(points)} points ({suffix_breakdown(points)})")
    if gone:
        print(f"  {gone} rows no longer in the export — kept, not deleted")
    report_skipped(skipped)


def seed_and_update(seed_path: str, osm_db_path: str, out_path: str) -> None:
    """Seed crop_labels from an already-labeled archive DB, then add any
    points newly present in a fresher OSM export that aren't in the archive
    yet. Existing labeled rows are copied verbatim — labels are never
    touched. climbing_paths is always rebuilt fresh from the OSM export,
    since it's derived/raw data, not something anyone labels by hand.

    crop_labels.id is AUTOINCREMENT and the labeling UI paginates by that
    id order, so page contents must stay stable as the DB grows: seed rows
    are (re-)inserted first, in their original id order, so they keep the
    same low ids; new points are appended strictly after, sorted by
    (image, position) for a readable grouping within that new tail — never
    interleaved into the middle of already-paginated pages.
    """
    path_rows, points, skipped = extract_from_osm(osm_db_path)

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
    for image, position in sorted(points):
        if (image, position) in existing_keys:
            continue
        p = points[(image, position)]
        dst.execute(
            "INSERT INTO crop_labels (image, position, cx, cy, osm_source) "
            "VALUES (?,?,?,?,?)",
            (image, position, p["cx"], p["cy"],
             json.dumps(p["sources"], ensure_ascii=False)),
        )
        n_new += 1

    dst.commit()
    seed.close()
    dst.close()

    print(f"Seeded from archive: {seed_path}")
    print(f"Updated from OSM export: {osm_db_path}")
    print(f"climbing_paths: {len(path_rows)} rows (fresh from export)")
    print(f"crop_labels:    {n_seeded} kept from archive (labels untouched) "
          f"+ {n_new} new points = {n_seeded + n_new} total")
    print(f"  in export: {len(points)} points ({suffix_breakdown(points)})")
    report_skipped(skipped)
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
                     help="build a NEW --out DB seeded from an already-labeled "
                          "archive DB, keeping its existing labels untouched, "
                          "add any points newly present in --osm-db, and "
                          "refresh climbing_paths from --osm-db")
    args = ap.parse_args()

    if not os.path.exists(args.osm_db):
        ap.error(f"OSM export not found: {args.osm_db} "
                 f"(run 01_download_osm.sh first, or pass --osm-db)")

    out_exists = os.path.exists(args.out)

    if args.seed and out_exists:
        # Seeding builds a DB from scratch; refuse rather than clobber labels.
        ap.error(f"--seed needs a fresh --out, but {args.out} already exists "
                 f"(run without --seed to update it in place)")

    if args.seed:
        seed_and_update(args.seed, args.osm_db, args.out)
        return

    # The existing DB may hold hand-made labels, so it is updated, not rebuilt.
    if out_exists:
        update_in_place(args.out, args.osm_db)
        return

    path_rows, points, skipped = extract_from_osm(args.osm_db)

    dst = sqlite3.connect(args.out)
    dst.executescript(SCHEMA_SQL)

    for row in path_rows:
        dst.execute("INSERT INTO climbing_paths VALUES (?,?,?,?)", row)

    for image, position in sorted(points):
        p = points[(image, position)]
        dst.execute(
            "INSERT INTO crop_labels (image, position, cx, cy, osm_source) "
            "VALUES (?,?,?,?,?)",
            (image, position, p["cx"], p["cy"],
             json.dumps(p["sources"], ensure_ascii=False)),
        )

    dst.commit()
    dst.close()

    n_refs = sum(len(p["sources"]) for p in points.values())
    print(f"climbing_paths: {len(path_rows)} rows")
    print(f"crop_labels:    {len(points)} points (deduped by image+position, "
          f"type=undecided)")
    print(f"  by suffix: {suffix_breakdown(points)}")
    print(f"  osm references: {n_refs} ({n_refs - len(points)} folded into "
          f"osm_source as 1:n)")
    report_skipped(skipped)
    print(f"Saved: {args.out}")


if __name__ == "__main__":
    main()
