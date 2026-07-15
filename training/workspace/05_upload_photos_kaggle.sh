#!/usr/bin/env bash
set -euo pipefail
# Step 05: Create the bolt-photos Kaggle dataset

PHOTOS_DIR="${PHOTOS_DIR:-data/photos}"
KAGGLE_USER="${KAGGLE_USER:-pavelzbytovsk}"

if [[ ! -d "$PHOTOS_DIR" ]]; then
  echo "ERROR: directory $PHOTOS_DIR does not exist. Run step 03 first." >&2; exit 1
fi

TMP=$(mktemp -d)
echo "Copying photos to $TMP..."
cp "$PHOTOS_DIR"/*.jpg "$TMP/" 2>/dev/null || true
cp "$PHOTOS_DIR"/*.jpeg "$TMP/" 2>/dev/null || true
cp "$PHOTOS_DIR"/*.png "$TMP/" 2>/dev/null || true

N=$(ls "$TMP" | wc -l)
echo "Photos: $N"

cat > "$TMP/dataset-metadata.json" <<EOF
{
  "title": "bolt-photos",
  "id": "${KAGGLE_USER}/bolt-photos",
  "licenses": [{"name": "CC0-1.0"}]
}
EOF

echo "Uploading dataset bolt-photos to Kaggle..."
# `kaggle datasets create` exits 0 even when it fails with "already in use" —
# it only prints the error to stdout — so we must check the text, not $?.
OUTPUT=$(kaggle datasets create -p "$TMP" --dir-mode zip 2>&1) || true
echo "$OUTPUT"
if echo "$OUTPUT" | grep -qi "already in use"; then
  echo "Dataset already exists — pushing a new version instead."
  kaggle datasets version -p "$TMP" -m "update $(date -u +%Y-%m-%dT%H:%M:%SZ)" --dir-mode zip
elif echo "$OUTPUT" | grep -qi "error"; then
  echo "ERROR: dataset upload failed" >&2
  exit 1
fi

rm -rf "$TMP"
echo "Done: https://www.kaggle.com/datasets/${KAGGLE_USER}/bolt-photos"
