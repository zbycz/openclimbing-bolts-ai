#!/usr/bin/env bash
set -euo pipefail
# Step 11: Run inference on all photos (Kaggle, CPU, ~30 minutes)

KAGGLE_USER="${KAGGLE_USER:-pavelzbytovsk}"
VERSION="${MODEL_VERSION:-v1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP=$(mktemp -d)

cp "$SCRIPT_DIR/11_bolt_infer_v1_kaggle.py" "$TMP/bolt_infer.py"

cat > "$TMP/kernel-metadata.json" <<EOF
{
  "id": "${KAGGLE_USER}/bolt-infer-${VERSION}",
  "title": "bolt-infer-${VERSION}",
  "code_file": "bolt_infer.py",
  "language": "python",
  "kernel_type": "script",
  "is_private": true,
  "enable_gpu": false,
  "enable_internet": true,
  "dataset_sources": [
    "${KAGGLE_USER}/bolt-photos",
    "${KAGGLE_USER}/bolt-model-${VERSION}"
  ],
  "competition_sources": [],
  "kernel_sources": []
}
EOF

echo "Launching kernel bolt-infer-${VERSION} on Kaggle..."
kaggle kernels push -p "$TMP"
rm -rf "$TMP"

echo ""
echo "Kernel launched (~30 minutes for 400 photos). Track progress:"
echo "  kaggle kernels status ${KAGGLE_USER}/bolt-infer-${VERSION}"
echo "  timeout 30 kaggle kernels logs --follow ${KAGGLE_USER}/bolt-infer-${VERSION}"
echo ""
echo "When it finishes, run step 12 (download detections)."
