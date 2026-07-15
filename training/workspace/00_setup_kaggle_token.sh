#!/usr/bin/env bash
set -euo pipefail
# Step 00: Set up the Kaggle API token
# Run once before the other steps.

echo "Find your Kaggle API token at: https://www.kaggle.com/settings  →  API  →  Create New Token"
echo "It will generate a kaggle.json file — copy the 'key' value from it."
echo ""
read -rp "Enter your Kaggle API token (KGAT_...): " TOKEN

if [[ -z "$TOKEN" ]]; then
  echo "ERROR: empty token" >&2; exit 1
fi

mkdir -p ~/.kaggle
echo "$TOKEN" > ~/.kaggle/access_token
chmod 600 ~/.kaggle/access_token

pip install -q kaggle
echo ""
echo "Verification:"
kaggle config view
