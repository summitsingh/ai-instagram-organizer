#!/usr/bin/env python3
"""Never-posted-twice registry for the trip-dump pipeline.

Standing project rule: only original, never-posted photos go into carousels.
This module enforces it with a small SQLite database keyed by perceptual hash
(dHash, the same 8x8 hash used by ``curate_carousel.dedup``), so a photo that
already went out in a published post can never be picked again — even across
trips, re-runs, and renamed files. Matching uses Hamming distance (<= 6 bits,
the same tolerance as dedup) because an assembled JPG slide and its original
HEIC/PNG rarely hash bit-identically.

Default database: ``~/.ai-instagram-organizer/posted.db`` (user-level, never
committed to the repo). Override with ``--registry``.

Usage:
    # after publishing a draft bundle:
    python -m trip_dumps mark-posted --draft-dir ./options/oahu \
        --post-url https://www.instagram.com/p/XXXX/

    # curate while skipping anything already posted:
    python trip_dumps/curate_carousel.py --source ~/photos --out-dir ./review \\
        --exclude-posted
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

EXTS = (".jpg", ".jpeg", ".png", ".webp")

SCHEMA = """
CREATE TABLE IF NOT EXISTS posted (
    dhash       TEXT PRIMARY KEY,   -- 16-char hex of the 64-bit dHash
    posted_at   TEXT NOT NULL,      -- UTC ISO timestamp of registration
    post_url    TEXT,               -- e.g. https://www.instagram.com/p/XXXX/
    post_id     TEXT,               -- shortcode / id when no URL is handy
    source_file TEXT,               -- original filename, for human debugging
    note        TEXT
)
"""


def default_db_path():
    """User-level registry location (never inside the repo)."""
    return os.path.join(os.path.expanduser("~"), ".ai-instagram-organizer", "posted.db")


def db_exists(db_path=None):
    db_path = db_path or default_db_path()
    return os.path.isfile(db_path)


def _connect(db_path, create=False):
    db_path = db_path or default_db_path()
    if not create and not os.path.isfile(db_path):
        return None
    if create:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path)
    if create:
        con.execute(SCHEMA)
        con.commit()
    return con


def dhash_of(path):
    """Perceptual hash of an image file, as 16-char hex. Reuses the
    canonical implementation in curate_carousel (lazy import: that module
    imports this one for --exclude-posted). Works whether this module is
    imported as part of the package or run as a script."""
    try:
        from .curate_carousel import dhash
    except ImportError:  # run as script: python trip_dumps/posted_registry.py
        from curate_carousel import dhash
    return f"{dhash(path):016x}"


def mark_posted(
    db_path=None,
    *,
    path=None,
    dhash_hex=None,
    post_url=None,
    post_id=None,
    source_file=None,
    note=None,
):
    """Register one photo as posted. Returns True if newly added,
    False if it was already in the registry (first record wins)."""
    db_path = db_path or default_db_path()
    if dhash_hex is None:
        if path is None:
            raise ValueError("mark_posted needs path or dhash_hex")
        dhash_hex = dhash_of(path)
    con = _connect(db_path, create=True)
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO posted "
            "(dhash, posted_at, post_url, post_id, source_file, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                dhash_hex,
                datetime.now(timezone.utc).isoformat(),
                post_url,
                post_id,
                source_file,
                note,
            ),
        )
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def _stored_hash_ints(db_path):
    """All registry hashes as ints. Empty list when there is no DB."""
    con = _connect(db_path or default_db_path())
    if con is None:
        return []
    try:
        return [
            int(r[0], 16) for r in con.execute("SELECT dhash FROM posted").fetchall()
        ]
    finally:
        con.close()


def _ham(a, b):
    return bin(a ^ b).count("1")


def is_posted(db_path, dhash_hex, threshold=6):
    """True if this hash — or a near-duplicate within ``threshold`` Hamming
    bits — is in the registry. The tolerance matters because the registered
    slide (assembled JPG) and a future candidate (original HEIC/PNG) rarely
    hash bit-identically. No DB file -> False."""
    target = int(dhash_hex, 16)
    return any(_ham(target, h) <= threshold for h in _stored_hash_ints(db_path))


def posted_hashes(db_path, hexes, threshold=6):
    """Batch lookup: return the subset of ``hexes`` matching the registry
    within ``threshold`` Hamming bits (same tolerance as dedup)."""
    hexes = set(hexes)
    if not hexes:
        return set()
    stored = _stored_hash_ints(db_path)
    if not stored:
        return set()
    return {
        hx for hx in hexes if any(_ham(int(hx, 16), h) <= threshold for h in stored)
    }


def filter_unposted(pairs, db_path=None, threshold=6):
    """Drop already-posted items. ``pairs`` is an iterable of
    (item, dhash_hex); returns the unposted (item, dhash_hex) pairs."""
    pairs = list(pairs)
    posted = posted_hashes(
        db_path or default_db_path(), {hx for _, hx in pairs}, threshold
    )
    return [(item, hx) for item, hx in pairs if hx not in posted]


def mark_draft_dir(db_path, draft_dir, post_url=None, post_id=None):
    """Register every image in a draft bundle dir (options/<trip>/) as posted.
    Returns (newly_added, total)."""
    try:
        names = sorted(
            fn
            for fn in os.listdir(draft_dir)
            if fn.lower().endswith(EXTS) and os.path.isfile(os.path.join(draft_dir, fn))
        )
    except FileNotFoundError:
        raise SystemExit(f"draft dir not found: {draft_dir}")
    added = 0
    for fn in names:
        if mark_posted(
            db_path,
            path=os.path.join(draft_dir, fn),
            post_url=post_url,
            post_id=post_id,
            source_file=fn,
        ):
            added += 1
    return added, len(names)


def count(db_path=None):
    con = _connect(db_path or default_db_path())
    if con is None:
        return 0
    try:
        return con.execute("SELECT COUNT(*) FROM posted").fetchone()[0]
    finally:
        con.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--draft-dir", required=True, help="draft bundle dir, e.g. ./options/oahu"
    )
    ap.add_argument("--post-url", help="published post URL")
    ap.add_argument("--post-id", help="post shortcode/id when no URL is handy")
    ap.add_argument(
        "--registry",
        help="registry DB path (default: " "~/.ai-instagram-organizer/posted.db)",
    )
    args = ap.parse_args(argv)
    added, total = mark_draft_dir(
        args.registry, args.draft_dir, post_url=args.post_url, post_id=args.post_id
    )
    print(
        f"MARK_POSTED draft_dir={args.draft_dir} "
        f"new={added} total={total} already={total - added} "
        f"registry={args.registry or default_db_path()}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
