#!/usr/bin/env bash
set -euo pipefail
# Step 12: Download detections_v1.sqlite from Kaggle

KAGGLE_USER="${KAGGLE_USER:-pavelzbytovsk}"
VERSION="${MODEL_VERSION:-v1}"
OUT="${MODEL_DIR:-data/model}"
mkdir -p "$OUT"

STATUS=$(kaggle kernels status "${KAGGLE_USER}/bolt-infer-${VERSION}" 2>&1)
echo "Kernel status: $STATUS"

if ! echo "$STATUS" | grep -qi "complete"; then
  echo "Kernel has not finished yet or has failed." >&2
  echo "  kaggle kernels logs ${KAGGLE_USER}/bolt-infer-${VERSION}" >&2
  exit 1
fi

echo "Downloading outputs → $OUT"
kaggle kernels output "${KAGGLE_USER}/bolt-infer-${VERSION}" -p "$OUT"

echo ""
echo "Downloaded files:"
ls -lh "$OUT"

if [[ -f "$OUT/detections_${VERSION}.sqlite" ]]; then
  python3 - <<PYEOF
import sqlite3
conn = sqlite3.connect("$OUT/detections_${VERSION}.sqlite")
total = conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
imgs  = conn.execute("SELECT COUNT(DISTINCT image) FROM detections").fetchone()[0]
print(f"Total detections: {total} from {imgs} images")
for k,v in conn.execute("SELECT key,value FROM meta ORDER BY key"): print(f"  {k}: {v}")
PYEOF
fi
