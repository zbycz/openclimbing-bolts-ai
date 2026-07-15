#!/usr/bin/env python3
"""
Step 03: Download full-quality Wikimedia Commons photos referenced in crop_labels.

Based on the original bolts_photo_down.py. URL construction mirrors
getCommonsImageUrl.ts (direct upload.wikimedia.org path via md5 of the
underscore-form title), so it's one request per photo.

Filenames: Commons stores files under the underscore form ("Foo_Bar.jpg"), but
the labeling app / DB refer to them by the display form with spaces
("Foo Bar.jpg", i.e. image.removeprefix("File:")). We therefore build the URL
from the underscore form and save the local file under the space form, so the
app finds the photo with no renaming.

Delays: 60s between files, 3600s (1h) on HTTP 429 / block. Idempotent: files
already present (non-empty) on disk are skipped, so re-running only fetches
what's still missing.

Usage:
  python3 03_download_photos.py
  python3 03_download_photos.py --db data/climbing_paths.sqlite --out data/photos
"""
import argparse
import hashlib
import os
import sqlite3
import subprocess
import time
import urllib.parse

DELAY_NORMAL = 60       # seconds between successful downloads
DELAY_BLOCK  = 3600     # seconds to wait when rate-limited / blocked
MAX_RETRIES  = 5        # retries per file before giving up

# Wikimedia serves upload.wikimedia.org fine with a browser-like User-Agent.
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"


def commons_url(file_tag: str) -> tuple[str, str]:
    """Return (download_url, local_filename) for a 'File:Foo Bar.jpg' tag.

    URL uses the underscore form (Commons storage); the local filename keeps
    spaces so it matches what the labeling app / crop_labels expect.
    """
    display = file_tag.removeprefix("File:")                 # "Foo Bar.jpg"
    us = urllib.parse.unquote(display).replace(" ", "_")     # "Foo_Bar.jpg"
    digest = hashlib.md5(us.encode("utf-8")).hexdigest()
    a, ab = digest[0], digest[0:2]
    encoded = urllib.parse.quote(us, safe="()!*'-._~")
    url = f"https://upload.wikimedia.org/wikipedia/commons/{a}/{ab}/{encoded}"
    return url, display


def download_file(url: str, dest: str) -> bool:
    """Download url to dest via curl. True on success, False on HTTP 429
    (rate-limited), raises on permanent errors."""
    tmp = dest + ".part"
    result = subprocess.run(
        ["curl", "-sSL", "--fail-with-body", "-A", UA,
         "-o", tmp, "-w", "%{http_code}", "--max-time", "120", url],
        capture_output=True, text=True,
    )
    http_code = result.stdout.strip()

    if result.returncode == 0 and http_code == "200":
        os.replace(tmp, dest)
        return True

    if os.path.exists(tmp):
        os.remove(tmp)

    if http_code == "429":
        return False  # rate limited — caller will wait

    raise RuntimeError(
        f"curl exited {result.returncode}, HTTP {http_code}: {result.stderr.strip()}")


def get_images(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    images = [r[0] for r in conn.execute(
        "SELECT DISTINCT image FROM crop_labels ORDER BY image")]
    conn.close()
    return images


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/climbing_paths.sqlite")
    ap.add_argument("--out", default="data/photos")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    images = get_images(args.db)
    total = len(images)
    print(f"Photos to download: {total}", flush=True)

    skipped = downloaded = failed = 0

    for idx, file_tag in enumerate(images, 1):
        url, filename = commons_url(file_tag)
        dest = os.path.join(args.out, filename)

        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            skipped += 1
            continue

        print(f"[{idx}/{total}] Downloading: {filename}", flush=True)

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if download_file(url, dest):
                    size = os.path.getsize(dest)
                    print(f"         OK ({size:,} bytes)", flush=True)
                    downloaded += 1
                    success = True
                    break
                print(f"         Rate-limited (attempt {attempt}). "
                      f"Waiting {DELAY_BLOCK // 3600}h …", flush=True)
                time.sleep(DELAY_BLOCK)
            except Exception as e:
                print(f"         Error: {e} (attempt {attempt})", flush=True)
                time.sleep(DELAY_BLOCK)

        if not success and not os.path.exists(dest):
            print(f"         FAILED after {MAX_RETRIES} attempts.", flush=True)
            failed += 1

        if idx < total and success:
            time.sleep(DELAY_NORMAL)

    print(f"\nDone: {downloaded} downloaded, {skipped} skipped, {failed} failed",
          flush=True)


if __name__ == "__main__":
    main()
