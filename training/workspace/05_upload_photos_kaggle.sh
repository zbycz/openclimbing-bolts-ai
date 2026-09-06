#!/usr/bin/env bash
set -euo pipefail
# Step 05: Create the bolt-photos-v2 Kaggle dataset
#
# v2 exists because Kaggle rewrites filenames it does not like: it drops
# non-ASCII characters and commas, so "Jickovice - Hlavní oblast, levá část.jpg"
# arrives as "Jickovice - Hlavn oblast lev st.jpg" and no lookup by the original
# name finds it again. Photos are uploaded under slugs instead, plus a
# filemap.json that maps each slug back to its Commons title.

PHOTOS_DIR="${PHOTOS_DIR:-data/photos}"
KAGGLE_USER="${KAGGLE_USER:-pavelzbytovsk}"
DATASET="${PHOTOS_DATASET:-bolt-photos-v2}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -d "$PHOTOS_DIR" ]]; then
  echo "ERROR: directory $PHOTOS_DIR does not exist. Run step 03 first." >&2; exit 1
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Hard links, not copies: the photos are 3.4 GB and the container has ~7 GB
# free, which it also has to keep the labeling database alive on.
python3 "$SCRIPT_DIR/slugify.py" link "$PHOTOS_DIR" "$TMP" --map "$TMP/filemap.json"

cat > "$TMP/dataset-metadata.json" <<EOF
{
  "title": "${DATASET}",
  "id": "${KAGGLE_USER}/${DATASET}",
  "licenses": [{"name": "CC0-1.0"}]
}
EOF

echo "Uploading dataset ${DATASET} to Kaggle..."
# `kaggle datasets create` exits 0 even when it fails with "already in use" —
# it only prints the error to stdout — so we must check the text, not $?.
OUTPUT=$(kaggle datasets create -p "$TMP" 2>&1) || true
echo "$OUTPUT"
if echo "$OUTPUT" | grep -qi "already in use"; then
  echo "Dataset already exists — pushing a new version instead."
  kaggle datasets version -p "$TMP" -m "update $(date -u +%Y-%m-%dT%H:%M:%SZ)"
elif echo "$OUTPUT" | grep -qi "error"; then
  echo "ERROR: dataset upload failed" >&2
  exit 1
fi

echo "Done: https://www.kaggle.com/datasets/${KAGGLE_USER}/${DATASET}"
