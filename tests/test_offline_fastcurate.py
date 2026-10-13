"""Offline fast curation tests using synthetic images only."""

import json
import sys

import pytest
from PIL import Image, ImageFilter

from trip_dumps import fast_curate
from trip_dumps.curate_carousel import dhash, ham


def _pattern_image(path, seed_a=3, seed_b=7):
    image = Image.new("RGB", (64, 64))
    image.putdata(
        [
            (
                (x * seed_a + y * seed_b) % 180 + 35,
                (x * (seed_a + 6) + y * (seed_b + 2)) % 180 + 35,
                (x * (seed_a + 12) + y * (seed_b + 8)) % 180 + 35,
            )
            for y in range(64)
            for x in range(64)
        ]
    )
    image.save(path)
    return image


def test_sharpness_ordering(tmp_path):
    image = _pattern_image(tmp_path / "sharp.png")
    assert fast_curate.sharpness(image) > fast_curate.sharpness(
        image.filter(ImageFilter.GaussianBlur(4))
    )


def test_exposure_limits():
    for level in (0, 255):
        result = fast_curate.exposure(Image.new("RGB", (32, 32), (level,) * 3))
        assert max(result.values()) > fast_curate.CLIP_LIMIT
    result = fast_curate.exposure(Image.new("RGB", (32, 32), (128,) * 3))
    assert max(result.values()) <= fast_curate.CLIP_LIMIT


def test_dhash_near_duplicates(tmp_path):
    a, b, c = (tmp_path / f"{name}.png" for name in "abc")
    image = _pattern_image(a)
    changed = image.copy()
    changed.putpixel((0, 0), (120, 120, 120))
    changed.save(b)
    _pattern_image(c, 17, 29)
    assert ham(dhash(a), dhash(b)) <= 6
    assert ham(dhash(a), dhash(c)) > 6


def test_manifest_thumbnails_and_assembly(tmp_path):
    source, out, draft = (tmp_path / name for name in ("source", "out", "draft"))
    source.mkdir()
    _pattern_image(source / "IMG_2026-03-25_10-00-00_000.png")
    assert (
        fast_curate.main(
            [
                "--source",
                str(source),
                "--out-dir",
                str(out),
                "--thumb-px",
                "40",
                "--no-face-check",
            ]
        )
        == 0
    )
    manifest = json.loads((out / "manifest.json").read_text())
    assert set(manifest) == {"config", "items"}
    item = manifest["items"][0]
    assert {
        "index",
        "source_path",
        "thumb",
        "sharpness",
        "exposure",
        "dhash",
        "score",
        "passed_face_check",
        "rejected",
    } <= set(item)
    assert item["passed_face_check"] is None
    with Image.open(out / item["thumb"]) as thumb:
        assert max(thumb.size) <= 40
    fast_curate.main(
        ["--assemble", "1", "--out-dir", str(out), "--draft-dir", str(draft)]
    )
    with Image.open(draft / "01.jpg") as slide:
        assert slide.size == (1080, 1350)
    assert (draft / "preview.jpg").exists()
    assert (
        json.loads((draft / "ASSEMBLY.json").read_text())["slides"][0]["manifest_index"]
        == 1
    )
    with pytest.raises(SystemExit):
        fast_curate.main(
            ["--assemble", "2", "--out-dir", str(out), "--draft-dir", str(draft)]
        )


def test_missing_cv2_requires_explicit_opt_out(tmp_path, monkeypatch):
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir()
    _pattern_image(source / "IMG_2026-03-25_10-00-00_000.png")
    monkeypatch.setitem(sys.modules, "cv2", None)
    with pytest.raises(SystemExit, match="opencv-python"):
        fast_curate.main(["--source", str(source), "--out-dir", str(out)])
    fast_curate.main(
        ["--source", str(source), "--out-dir", str(out), "--no-face-check"]
    )
    item = json.loads((out / "manifest.json").read_text())["items"][0]
    assert item["passed_face_check"] is None
