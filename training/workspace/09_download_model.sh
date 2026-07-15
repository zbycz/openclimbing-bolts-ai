#!/usr/bin/env bash
set -euo pipefail
# Step 09: Download the trained model from Kaggle

KAGGLE_USER="${KAGGLE_USER:-pavelzbytovsk}"
OUT="${MODEL_DIR:-data/model}"
mkdir -p "$OUT"

STATUS=$(kaggle kernels status "${KAGGLE_USER}/bolt-tile-train" 2>&1)
echo "Kernel status: $STATUS"

if echo "$STATUS" | grep -qi "error\|fail\|cancel"; then
  echo "ERROR: kernel failed. Check the logs:" >&2
  echo "  kaggle kernels logs ${KAGGLE_USER}/bolt-tile-train" >&2
  exit 1
fi

if ! echo "$STATUS" | grep -qi "complete"; then
  echo "Kernel has not finished yet. Check again in a moment." >&2
  exit 1
fi

echo "Downloading kernel outputs → $OUT"
kaggle kernels output "${KAGGLE_USER}/bolt-tile-train" -p "$OUT"

echo ""
echo "Downloaded files:"
ls -lh "$OUT"
