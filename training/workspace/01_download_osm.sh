#!/usr/bin/env bash
set -euo pipefail
# Step 01: Download the OSM database from openclimbing.org

OUTDIR="${DATA_DIR:-./data}"
mkdir -p "$OUTDIR"

FNAME="openclimbing_$(date +%Y-%m-%d_%H%M%S).sqlite"
OUTFILE="$OUTDIR/$FNAME"

echo "Downloading OSM database → $OUTFILE"
curl -X POST -L "https://openclimbing.org/api/climbing-tiles/export" \
  -o "$OUTFILE" \
  -w "HTTP %{http_code} — %{size_download} bytes\n"

ln -sf "$FNAME" "$OUTDIR/openclimbing_latest.sqlite"
echo "Done: $OUTFILE"
echo "Symlink: $OUTDIR/openclimbing_latest.sqlite"
