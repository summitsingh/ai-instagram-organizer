"""Offline unit tests for the posted registry (trip_dumps/posted_registry.py).

Pure-function tests only: no network, no live APIs, no sleeping. Synthetic
images and tmp_path fixtures throughout.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from trip_dumps.posted_registry import (
    count,
    db_exists,
    dhash_of,
    filter_unposted,
    is_posted,
    mark_draft_dir,
    mark_posted,
    posted_hashes,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _pattern_image(path, seed_a, seed_b):
    """Deterministic patterned image so dHash is stable and non-degenerate."""
    px = Image.new("RGB", (64, 64))
    data = [
        (
            (x * seed_a + y * seed_b) % 256,
            (x * (seed_a + 6) + y * (seed_b + 2)) % 256,
            (x * (seed_a + 12) + y * (seed_b + 8)) % 256,
        )
        for y in range(64)
        for x in range(64)
    ]
    px.putdata(data)
    px.save(path)


def _db(tmp_path):
    return str(tmp_path / "posted.db")


# ---------------------------------------------------------------------------
# hashing
# ---------------------------------------------------------------------------


def test_dhash_hex_format(tmp_path):
    p = tmp_path / "a.jpg"
    _pattern_image(p, 3, 7)
    hx = dhash_of(str(p))
    assert isinstance(hx, str) and len(hx) == 16
    int(hx, 16)  # valid hex


def test_dhash_stable_and_distinct(tmp_path):
    a, b, c = (tmp_path / f"{n}.jpg" for n in "abc")
    _pattern_image(a, 3, 7)
    _pattern_image(b, 3, 7)  # identical content
    _pattern_image(c, 11, 29)  # different content
    assert dhash_of(str(a)) == dhash_of(str(b))
    assert dhash_of(str(a)) != dhash_of(str(c))


# ---------------------------------------------------------------------------
# registry CRUD
# ---------------------------------------------------------------------------


def test_missing_db_reports_empty(tmp_path):
    db = _db(tmp_path)
    assert not db_exists(db)
    assert not is_posted(db, "0" * 16)
    assert posted_hashes(db, {"0" * 16}) == set()
    assert count(db) == 0


def test_mark_and_query(tmp_path):
    db, img = _db(tmp_path), tmp_path / "a.jpg"
    _pattern_image(img, 3, 7)
    assert (
        mark_posted(db, path=str(img), post_url="https://ig/p/X/", source_file="a.jpg")
        is True
    )
    assert db_exists(db)
    assert is_posted(db, dhash_of(str(img)))
    assert not is_posted(db, "f" * 16)
    assert count(db) == 1


def test_duplicate_mark_keeps_first(tmp_path):
    db, img = _db(tmp_path), tmp_path / "a.jpg"
    _pattern_image(img, 3, 7)
    assert mark_posted(db, path=str(img), post_url="https://ig/p/ONE/") is True
    # second registration is a no-op; the first URL wins
    assert mark_posted(db, path=str(img), post_url="https://ig/p/TWO/") is False
    con = sqlite3.connect(db)
    try:
        url = con.execute("SELECT post_url FROM posted").fetchone()[0]
    finally:
        con.close()
    assert url == "https://ig/p/ONE/"
    assert count(db) == 1


def test_mark_by_explicit_hash(tmp_path):
    db = _db(tmp_path)
    assert mark_posted(db, dhash_hex="ab" * 8, post_id="XYZ") is True
    assert is_posted(db, "ab" * 8)


def test_posted_hashes_batch(tmp_path):
    db = _db(tmp_path)
    imgs = []
    for i, (a, b) in enumerate([(3, 7), (11, 29), (5, 13)]):
        p = tmp_path / f"{i}.jpg"
        _pattern_image(p, a, b)
        imgs.append(p)
    for p in imgs[:2]:
        mark_posted(db, path=str(p))
    hexes = {dhash_of(str(p)) for p in imgs}
    found = posted_hashes(db, hexes)
    assert found == {dhash_of(str(p)) for p in imgs[:2]}
    assert posted_hashes(db, set()) == set()


# ---------------------------------------------------------------------------
# draft-dir marking + filtering
# ---------------------------------------------------------------------------


def test_mark_draft_dir(tmp_path):
    db = _db(tmp_path)
    draft = tmp_path / "options" / "oahu"
    draft.mkdir(parents=True)
    for i, (a, b) in enumerate([(3, 7), (11, 29)]):
        _pattern_image(draft / f"{i + 1:02d}.jpg", a, b)
    (draft / "OPTIONS.md").write_text("notes")  # non-image is ignored
    added, total = mark_draft_dir(db, str(draft), post_url="https://ig/p/OAHU/")
    assert (added, total) == (2, 2)
    # idempotent: second run adds nothing
    added2, total2 = mark_draft_dir(db, str(draft), post_url="https://ig/p/OAHU/")
    assert (added2, total2) == (0, 2)
    assert count(db) == 2


def test_filter_unposted(tmp_path):
    db = _db(tmp_path)
    posted_img = tmp_path / "posted.jpg"
    fresh_img = tmp_path / "fresh.jpg"
    _pattern_image(posted_img, 3, 7)
    _pattern_image(fresh_img, 11, 29)
    mark_posted(db, path=str(posted_img))
    pairs = [
        ("posted-item", dhash_of(str(posted_img))),
        ("fresh-item", dhash_of(str(fresh_img))),
    ]
    assert filter_unposted(pairs, db) == [("fresh-item", dhash_of(str(fresh_img)))]
    # missing DB -> nothing filtered
    assert filter_unposted(pairs, _db(tmp_path / "nodir")) == pairs


def test_near_duplicate_matches_across_formats(tmp_path):
    """A PNG original and its JPG re-save (like an assembled slide) must
    match: this is the real mark-posted -> --exclude-posted path."""
    db = _db(tmp_path)
    src = tmp_path / "orig.png"
    _pattern_image(src, 3, 7)
    slide = tmp_path / "slide.jpg"
    Image.open(src).convert("RGB").save(slide, quality=93)
    mark_posted(db, path=str(slide), source_file="01.jpg")
    assert is_posted(db, dhash_of(str(src)))
    assert filter_unposted([("x", dhash_of(str(src)))], db) == []
