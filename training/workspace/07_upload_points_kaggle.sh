#!/usr/bin/env bash
set -euo pipefail
# Step 07: Create the bolt-points Kaggle dataset

POINTS_DIR="${POINTS_DIR:-data/bolt-points}"
KAGGLE_USER="${KAGGLE_USER:-pavelzbytovsk}"

if [[ ! -f "$POINTS_DIR/points.json" ]]; then
  echo "ERROR: $POINTS_DIR/points.json does not exist. Run step 06 first." >&2; exit 1
fi

cat > "$POINTS_DIR/dataset-metadata.json" <<EOF
{
  "title": "bolt-points",
  "id": "${KAGGLE_USER}/bolt-points",
  "licenses": [{"name": "CC0-1.0"}]
}
EOF

echo "Uploading dataset bolt-points to Kaggle..."
# `kaggle datasets create` exits 0 even when it fails with "already in use" —
# it only prints the error to stdout — so we must check the text, not $?.
OUTPUT=$(kaggle datasets create -p "$POINTS_DIR" --dir-mode zip 2>&1) || true
echo "$OUTPUT"
if echo "$OUTPUT" | grep -qi "already in use"; then
  echo "Dataset already exists — pushing a new version instead."
  kaggle datasets version -p "$POINTS_DIR" -m "update $(date -u +%Y-%m-%dT%H:%M:%SZ)" --dir-mode zip
elif echo "$OUTPUT" | grep -qi "error"; then
  echo "ERROR: dataset upload failed" >&2
  exit 1
fi

echo "Done: https://www.kaggle.com/datasets/${KAGGLE_USER}/bolt-points"
echo ""
echo "Kaggle needs a moment to process the dataset before running the kernel."
echo "Check status: kaggle datasets status ${KAGGLE_USER}/bolt-points"
