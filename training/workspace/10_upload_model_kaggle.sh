#!/usr/bin/env bash
set -euo pipefail
# Step 10: Upload the model as Kaggle dataset bolt-model-v1

MODEL_DIR="${MODEL_DIR:-data/model}"
KAGGLE_USER="${KAGGLE_USER:-pavelzbytovsk}"
VERSION="${MODEL_VERSION:-v1}"

ONNX="$MODEL_DIR/best.onnx"
if [[ ! -f "$ONNX" ]]; then
  echo "ERROR: $ONNX does not exist. Run step 09 first." >&2; exit 1
fi

TMP=$(mktemp -d)
cp "$ONNX" "$TMP/best_${VERSION}.onnx"

cat > "$TMP/dataset-metadata.json" <<EOF
{
  "title": "bolt-model-${VERSION}",
  "id": "${KAGGLE_USER}/bolt-model-${VERSION}",
  "licenses": [{"name": "CC0-1.0"}]
}
EOF

echo "Uploading model bolt-model-${VERSION} to Kaggle..."
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

echo "Done: https://www.kaggle.com/datasets/${KAGGLE_USER}/bolt-model-${VERSION}"
echo ""
echo "Kaggle needs a moment to process. Then run step 11 (inference)."
