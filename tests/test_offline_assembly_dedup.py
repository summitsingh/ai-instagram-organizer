"""Crop and source registry coverage using synthetic images only."""

import json
import logging

from PIL import Image, ImageOps

from trip_dumps import fast_curate, posted_registry


def test_source_hash_closes_crop_gap(tmp_path):
    source, review, draft = (tmp_path / name for name in ("source", "review", "draft"))
    source.mkdir()
    original = source / "IMG_2026-03-25_10-00-00_000.png"
    image = Image.new("RGB", (360, 240))
    image.putdata(
        [
            (
                (x * 17 + y * 7) % 180 + 35,
                (x * 5 + y * 23) % 180 + 35,
                (x * 11 + y * 13) % 180 + 35,
            )
            for y in range(240)
            for x in range(360)
        ]
    )
    image.save(original)
    assert (
        fast_curate.main(
            ["--source", str(source), "--out-dir", str(review), "--no-face-check"]
        )
        == 0
    )
    assert (
        fast_curate.main(
            ["--out-dir", str(review), "--assemble", "1", "--draft-dir", str(draft)]
        )
        == 0
    )
    entry = json.loads((draft / "ASSEMBLY.json").read_text())["slides"][0]
    assert entry["source_dhash"] == posted_registry.dhash_of(original)
    assert len(entry["source_dhash"]) == 16

    crop = tmp_path / "crop" / original.name
    crop.parent.mkdir()
    ImageOps.fit(image, (1080, 1350)).save(crop)
    assert (
        posted_registry._ham(
            int(entry["source_dhash"], 16), int(posted_registry.dhash_of(crop), 16)
        )
        > 6
    )

    registry = tmp_path / "posted.db"
    added, total = posted_registry.mark_draft_dir(str(registry), str(draft))
    assert (added, total) == (2, 2)
    assert posted_registry.is_posted(str(registry), posted_registry.dhash_of(original))
    later = tmp_path / "later"
    assert (
        fast_curate.main(
            [
                "--source",
                str(source),
                "--out-dir",
                str(later),
                "--no-face-check",
                "--exclude-posted",
                "--registry",
                str(registry),
            ]
        )
        == 0
    )
    assert (
        json.loads((later / "screening-report.json").read_text())["posted_rejected"]
        == 1
    )


def test_draft_without_assembly_registers_only_numbered_slides(tmp_path):
    draft = tmp_path / "draft"
    draft.mkdir()
    for name, color in (("01.jpg", "red"), ("preview.jpg", "blue")):
        Image.new("RGB", (32, 32), color).save(draft / name)
    registry = tmp_path / "posted.db"
    assert posted_registry.mark_draft_dir(str(registry), str(draft)) == (1, 1)
    assert posted_registry.count(str(registry)) == 1


def test_malformed_assembly_falls_back_to_slides(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    draft = tmp_path / "draft"
    draft.mkdir()
    Image.new("RGB", (32, 32), "red").save(draft / "01.jpg")
    (draft / "ASSEMBLY.json").write_text("{bad json")
    registry = tmp_path / "posted.db"
    assert posted_registry.mark_draft_dir(str(registry), str(draft)) == (1, 1)
    assert posted_registry.count(str(registry)) == 1
    assert "ignoring malformed ASSEMBLY.json" in caplog.text
