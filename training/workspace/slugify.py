#!/usr/bin/env python3
"""Turn a Wikimedia photo title into a filename that survives a round trip.

Kaggle rewrites the names of uploaded files. It drops every non-ASCII
character and also removes commas, so

    Jickovice - Hlavní oblast, levá část.jpg
      lands on the dataset as
    Jickovice - Hlavn oblast lev st.jpg

Guessing that transform from the outside was how photos ended up silently
missing from training: the kernel looked up the original name, found nothing
and skipped the photo. So instead of guessing, we hand Kaggle names it has no
reason to touch — lowercase ASCII letters, digits and hyphens:

    žluťoučký kůň.JPG  ->  zlutoucky-kun.jpg

The original title stays the source of truth in the database and in
filemap.json, which travels with the photos dataset.
"""
import argparse
import json
import os
import re
import shutil
import sys
import unicodedata

# Letters that NFKD leaves alone because they are not an accented base letter
# plus a mark — they are their own letter. Without these, ß would vanish and
# Weißenstein would collide with Weienstein.
SPECIAL = {
    "ß": "ss", "ı": "i", "ø": "o", "Ø": "o", "ł": "l", "Ł": "l",
    "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "đ": "d", "Đ": "d",
    "ð": "d", "Ð": "d", "þ": "th", "Þ": "th", "ħ": "h", "ŋ": "ng",
}


def slugify(text):
    """ASCII slug: lowercase, words joined by single hyphens."""
    text = "".join(SPECIAL.get(c, c) for c in text)
    # NFKD splits "á" into "a" + combining acute, which Mn then removes.
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def slug_filename(name):
    """Slug of a filename, keeping a lowercased extension: a.JPG -> a.jpg."""
    stem, ext = os.path.splitext(name)
    stem = slugify(stem)
    ext = "." + slugify(ext) if ext else ""
    return (stem or "photo") + ext


def slug_map(names):
    """{original: slug} for a whole set of names.

    Raises on a collision rather than picking a winner. Two photos sharing a
    slug would mean one silently overwrites the other on upload, and no
    numbering scheme fixes it safely: step 05 slugs the photo directory and
    step 06 slugs the database, and those two sets are not identical, so any
    "-2" suffix would land on a different photo in each.
    """
    out, seen = {}, {}
    for n in sorted(names):
        s = slug_filename(n)
        if s in seen:
            raise SystemExit(
                "ERROR: two names slug to %r:\n  %s\n  %s\n"
                "Rename one of them on Commons, or extend slugify()." % (s, seen[s], n))
        seen[s] = n
        out[n] = s
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    lk = sub.add_parser("link", help="mirror a photo directory under slug names")
    lk.add_argument("src")
    lk.add_argument("dst")
    lk.add_argument("--map", default=None,
                    help="also write a slug -> original JSON map here")

    sl = sub.add_parser("slug", help="print the slug of each argument")
    sl.add_argument("names", nargs="+")

    args = ap.parse_args()

    if args.cmd == "slug":
        for n in args.names:
            print(slug_filename(n))
        return

    exts = {".jpg", ".jpeg", ".png"}
    names = [f for f in os.listdir(args.src)
             if os.path.splitext(f)[1].lower() in exts]
    if not names:
        sys.exit("ERROR: no photos in %s" % args.src)
    os.makedirs(args.dst, exist_ok=True)
    mapping = slug_map(names)
    for orig, slug in mapping.items():
        src, dst = os.path.join(args.src, orig), os.path.join(args.dst, slug)
        if os.path.exists(dst):
            os.unlink(dst)
        try:
            # A hard link costs no disk. The photos are 3.4 GB and the
            # container has 7.5 GB free, so a plain copy is not affordable.
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    if args.map:
        with open(args.map, "w", encoding="utf-8") as f:
            json.dump({v: k for k, v in mapping.items()}, f,
                      ensure_ascii=False, sort_keys=True, indent=1)
    print("Linked %d photos into %s" % (len(mapping), args.dst))


if __name__ == "__main__":
    main()
