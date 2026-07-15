#!/usr/bin/env bash
set -euo pipefail
# Step 08: Upload and launch the training kernel on Kaggle (GPU P100)
# Duration: ~2 hours. Follow logs with:
#   timeout 30 kaggle kernels logs --follow pavelzbytovsk/bolt-tile-train

KAGGLE_USER="${KAGGLE_USER:-pavelzbytovsk}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP=$(mktemp -d)

cp "$SCRIPT_DIR/08_yolo_tile_train_kaggle.py" "$TMP/yolo_tile_train.py"

cat > "$TMP/kernel-metadata.json" <<EOF
{
  "id": "${KAGGLE_USER}/bolt-tile-train",
  "title": "bolt-tile-train",
  "code_file": "yolo_tile_train.py",
  "language": "python",
  "kernel_type": "script",
  "is_private": true,
  "enable_gpu": true,
  "enable_internet": true,
  "dataset_sources": [
    "${KAGGLE_USER}/bolt-photos",
    "${KAGGLE_USER}/bolt-points"
  ],
  "competition_sources": [],
  "kernel_sources": []
}
EOF

echo "Launching kernel bolt-tile-train on Kaggle..."
kaggle kernels push -p "$TMP"
rm -rf "$TMP"

echo ""
echo "Kernel launched. Track progress:"
echo "  kaggle kernels status ${KAGGLE_USER}/bolt-tile-train"
echo "  timeout 30 kaggle kernels logs --follow ${KAGGLE_USER}/bolt-tile-train"
echo ""
echo "When it finishes, run step 09 (download the model)."
