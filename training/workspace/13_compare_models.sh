#!/usr/bin/env bash
set -euo pipefail
# Step 13: Score both trained models on the same held-out tiles.
#
# results.csv cannot answer "did the new data help?" — each run validated on its
# own split, and the July model trained on 29 of the 40 photos the September run
# held out. This uploads both checkpoints and scores them on the 11 photos
# neither model ever trained on (see splitcalc.py).

KAGGLE_USER="${KAGGLE_USER:-pavelzbytovsk}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OLD="${OLD_MODEL:-data/model-v1-2026-07-16/best.pt}"
NEW="${NEW_MODEL:-data/model/best.pt}"

for f in "$OLD" "$NEW"; do
  [[ -f "$f" ]] || { echo "ERROR: $f does not exist" >&2; exit 1; }
done

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
cp "$OLD" "$TMP/best_v1.pt"
cp "$NEW" "$TMP/best_v2.pt"
cat > "$TMP/dataset-metadata.json" <<EOF
{
  "title": "bolt-models-cmp",
  "id": "${KAGGLE_USER}/bolt-models-cmp",
  "licenses": [{"name": "CC0-1.0"}]
}
EOF

echo "Uploading both checkpoints..."
OUTPUT=$(kaggle datasets create -p "$TMP" 2>&1) || true
echo "$OUTPUT"
if echo "$OUTPUT" | grep -qi "already in use"; then
  kaggle datasets version -p "$TMP" -m "compare $(date -u +%Y-%m-%dT%H:%M:%SZ)"
elif echo "$OUTPUT" | grep -qi "error"; then
  echo "ERROR: upload failed" >&2; exit 1
fi

echo "Waiting for the dataset to become ready..."
for _ in $(seq 1 40); do
  s=$(kaggle datasets status "${KAGGLE_USER}/bolt-models-cmp" 2>&1 || true)
  echo "  status: $s"
  [[ "$s" == *ready* ]] && break
  sleep 15
done

K=$(mktemp -d); trap 'rm -rf "$TMP" "$K"' EXIT
cp "$SCRIPT_DIR/13_compare_models_kaggle.py" "$K/bolt_compare.py"
cat > "$K/kernel-metadata.json" <<EOF
{
  "id": "${KAGGLE_USER}/bolt-compare",
  "title": "bolt-compare",
  "code_file": "bolt_compare.py",
  "language": "python",
  "kernel_type": "script",
  "is_private": true,
  "enable_gpu": true,
  "enable_internet": true,
  "dataset_sources": [
    "${KAGGLE_USER}/bolt-photos-v2",
    "${KAGGLE_USER}/bolt-points",
    "${KAGGLE_USER}/bolt-models-cmp"
  ],
  "competition_sources": [],
  "kernel_sources": []
}
EOF

echo "Launching kernel bolt-compare..."
kaggle kernels push -p "$K"
echo ""
echo "  kaggle kernels status ${KAGGLE_USER}/bolt-compare"
echo "  kaggle kernels logs -f ${KAGGLE_USER}/bolt-compare"
