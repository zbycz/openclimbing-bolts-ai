# Step 04: Label All the Data

Run the labeling server locally and expose it via a Cloudflare tunnel so you can
review every bolt position on any device (phone, tablet). Confirmed / rejected
bolts are written back into `crop_labels` in `climbing_paths.sqlite`, so labeling
picks up exactly where step 02 left off.

```bash
# Dependencies (the server uses Python's stdlib http.server + Pillow)
pip install pillow

# Start the labeling server. It reads the bolt list and writes labels
# directly to the DB — no crops.json needed.
cd training/workspace/bolt-app
BOLT_DB="$PWD/../data/climbing_paths.sqlite" \
BOLT_IMG_DIR="$PWD/../data/photos" \
python3 server.py
# Serves on http://0.0.0.0:8001  (open /crops)
```

```bash
# In a second terminal — expose it via a Cloudflare tunnel (no account needed)
cloudflared tunnel --url http://localhost:8001
# Prints a public https://xxx.trycloudflare.com URL — open it on any device
```

Swipe/click each crop to mark it **bolt** ✓ or **no-bolt** ✕; the sliders
fine-tune the exact centre and radius. Everything is saved live to
`crop_labels`, which is what step 06 exports for training.
