"""Offline unit tests for the trip-dump pipeline (trip_dumps/).

Pure-function tests only: no network, no live APIs, no sleeping. Synthetic
inputs and tmp_path fixtures throughout.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from trip_dumps import cluster_trips, curate_carousel

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _item(dt, lat=None, lon=None):
    return {"dt": dt, "lat": lat, "lon": lon, "file": "x.jpg"}


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


# ---------------------------------------------------------------------------
# filename-timestamp date parsing (cluster_trips.fname_dt / curate fname_dt)
# ---------------------------------------------------------------------------


class TestFnameDt:
    def test_valid_stamp(self):
        dt = cluster_trips.fname_dt("IMG_2025-02-13_09-03-08_000.jpg")
        assert dt == datetime(2025, 2, 13, 9, 3, 8)

    def test_valid_stamp_no_millis(self):
        dt = cluster_trips.fname_dt("PXL_2025-12-07_16-26-59.jpg")
        assert dt == datetime(2025, 12, 7, 16, 26, 59)

    def test_valid_stamp_embedded_in_longer_name(self):
        dt = cluster_trips.fname_dt(
            "20250301_120000_2025-02-13_09-03-08_000-edited.jpg"
        )
        assert dt == datetime(2025, 2, 13, 9, 3, 8)

    def test_no_pattern_returns_none(self):
        assert cluster_trips.fname_dt("photo.jpg") is None
        assert cluster_trips.fname_dt("IMG_20250213.jpg") is None

    def test_impossible_date_returns_none(self):
        # Regex matches, but datetime() raises ValueError -> None.
        assert cluster_trips.fname_dt("IMG_2025-13-99_99-99-99_000.jpg") is None

    def test_curate_fname_dt_matches_cluster(self):
        name = "IMG_2025-02-13_09-03-08_000.jpg"
        assert curate_carousel.fname_dt(name) == cluster_trips.fname_dt(name)
        assert curate_carousel.fname_dt("photo.jpg") is None


# ---------------------------------------------------------------------------
# trip clustering / splitting on synthetic photo lists
# ---------------------------------------------------------------------------


class TestTemporalClusters:
    def test_single_cluster_when_no_gap(self):
        items = [_item(datetime(2025, 2, 13) + timedelta(hours=h)) for h in (0, 5, 20)]
        clusters = cluster_trips.temporal_clusters(items, gap_days=3)
        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_gap_splits_trips(self):
        items = [
            _item(datetime(2025, 2, 13, 10)),
            _item(datetime(2025, 2, 14, 10)),
            _item(datetime(2025, 3, 1, 10)),  # 15-day gap -> new trip
            _item(datetime(2025, 3, 2, 10)),
        ]
        clusters = cluster_trips.temporal_clusters(items, gap_days=3)
        assert [len(c) for c in clusters] == [2, 2]

    def test_gap_boundary_is_inclusive(self):
        # A gap of exactly >= gap_days starts a new trip.
        items = [
            _item(datetime(2025, 2, 13, 10)),
            _item(datetime(2025, 2, 16, 10)),
        ]  # exactly 3 days later
        clusters = cluster_trips.temporal_clusters(items, gap_days=3)
        assert len(clusters) == 2

    def test_custom_gap_days(self):
        items = [
            _item(datetime(2025, 2, 13, 10)),
            _item(datetime(2025, 2, 16, 10)),
        ]  # 3-day gap
        assert len(cluster_trips.temporal_clusters(items, gap_days=3)) == 2
        assert len(cluster_trips.temporal_clusters(items, gap_days=4)) == 1


class TestGeoSplit:
    OAHU = (21.3, -157.8)
    NYC = (40.7, -74.0)

    def _trip(self, coords):
        base = datetime(2025, 2, 13, 10)
        return [
            _item(base + timedelta(hours=i), lat, lon)
            for i, (lat, lon) in enumerate(coords)
        ]

    def test_sustained_jump_splits(self):
        items = self._trip([self.OAHU, self.OAHU, self.NYC, self.NYC])
        trips = cluster_trips.geo_split_trips([items], geo_split_km=150)
        assert len(trips) == 2
        assert len(trips[0]) == 2 and len(trips[1]) == 2

    def test_one_off_bad_gps_fix_stays_with_prior_location(self):
        # Oahu -> one bad NYC fix -> back to Oahu: the fix is absorbed into
        # the preceding trip (it must NOT start a trip of its own).
        items = self._trip([self.OAHU, self.NYC, self.OAHU, self.OAHU])
        trips = cluster_trips.geo_split_trips([items], geo_split_km=150)
        assert len(trips) == 2
        assert [(it["lat"], it["lon"]) for it in trips[0]] == [self.OAHU, self.NYC]
        assert all((it["lat"], it["lon"]) == self.OAHU for it in trips[1])

    def test_missing_gps_never_splits(self):
        items = self._trip([(None, None)] * 4)
        trips = cluster_trips.geo_split_trips([items], geo_split_km=150)
        assert len(trips) == 1

    def test_small_jumps_do_not_split(self):
        near = [(21.30 + i * 0.01, -157.80) for i in range(4)]  # ~1 km steps
        trips = cluster_trips.geo_split_trips([self._trip(near)], geo_split_km=150)
        assert len(trips) == 1


class TestParseBox:
    def test_valid_box(self):
        assert cluster_trips.parse_box("21.2,21.8,-158.35,-157.6") == (
            21.2,
            21.8,
            -158.35,
            -157.6,
        )

    def test_invalid_box_raises(self):
        import pytest

        with pytest.raises(ValueError):
            cluster_trips.parse_box("21.2,21.8,-158.35")


# ---------------------------------------------------------------------------
# cluster_trips end-to-end on a synthetic source dir (exercises main(argv))
# ---------------------------------------------------------------------------


class TestClusterMain:
    def test_main_splits_two_date_ranges(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for d, n in (("2025-02-13", 3), ("2025-03-01", 3)):
            y, m, day = d.split("-")
            for i in range(n):
                (src / f"IMG_{y}-{m}-{day}_10-0{i}-00_000.jpg").touch()
        out_json = tmp_path / "trips.json"
        out_md = tmp_path / "trips.md"
        rc = cluster_trips.main(
            [
                "--source",
                str(src),
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
                "--min-photos",
                "1",
            ]
        )
        assert rc is None  # success path returns None
        results = json.loads(out_json.read_text())
        assert len(results) == 2
        assert [r["count"] for r in results] == [3, 3]
        assert results[0]["start"] == "2025-02-13"
        assert results[1]["start"] == "2025-03-01"
        assert out_md.read_text().startswith("# Trip Dump Candidates")

    def test_main_no_photos_returns_1(self, tmp_path):
        src = tmp_path / "empty"
        src.mkdir()
        rc = cluster_trips.main(
            [
                "--source",
                str(src),
                "--out-json",
                str(tmp_path / "t.json"),
                "--out-md",
                str(tmp_path / "t.md"),
            ]
        )
        assert rc == 1


# ---------------------------------------------------------------------------
# dHash dedup on synthetic images
# ---------------------------------------------------------------------------


class TestDhash:
    def test_identical_images_hash_equal(self, tmp_path):
        p1, p2 = tmp_path / "a.png", tmp_path / "b.png"
        _pattern_image(p1, 5, 3)
        _pattern_image(p2, 5, 3)
        assert curate_carousel.dhash(str(p1)) == curate_carousel.dhash(str(p2))
        assert (
            curate_carousel.ham(
                curate_carousel.dhash(str(p1)), curate_carousel.dhash(str(p2))
            )
            == 0
        )

    def test_different_images_hash_differ(self, tmp_path):
        p1, p2 = tmp_path / "a.png", tmp_path / "b.png"
        _pattern_image(p1, 5, 3)
        _pattern_image(p2, 41, 17)
        h1, h2 = curate_carousel.dhash(str(p1)), curate_carousel.dhash(str(p2))
        assert h1 != h2
        assert curate_carousel.ham(h1, h2) > 6

    def test_ham_counts_bits(self):
        assert curate_carousel.ham(0b1010, 0b1000) == 1
        assert curate_carousel.ham(0xFF, 0x00) == 8
        assert curate_carousel.ham(12345, 12345) == 0


class TestDedup:
    def _cand(self, path, dt):
        return {
            "fn": path.name,
            "path": str(path),
            "dt": dt,
            "gps": None,
            "bytes": path.stat().st_size,
        }

    def test_dedup_keeps_largest_of_near_duplicates(self, tmp_path):
        big, small, other = (
            tmp_path / "big.bmp",
            tmp_path / "small.png",
            tmp_path / "other.png",
        )
        _pattern_image(big, 5, 3)  # same pixels as small.png, larger file
        _pattern_image(small, 5, 3)
        _pattern_image(other, 41, 17)  # genuinely different
        base = datetime(2025, 2, 13, 10)
        cands = [
            self._cand(big, base),
            self._cand(small, base + timedelta(minutes=1)),
            self._cand(other, base + timedelta(minutes=2)),
        ]
        kept = curate_carousel.dedup(cands, threshold=6, out_dir=str(tmp_path))
        kept_names = {c["fn"] for c, _ in kept}
        assert kept_names == {"big.bmp", "other.png"}  # small.png dropped

    def test_dedup_keeps_everything_when_all_different(self, tmp_path):
        paths = []
        for i, (a, b) in enumerate([(5, 3), (41, 17), (7, 29)]):
            p = tmp_path / f"img{i}.png"
            _pattern_image(p, a, b)
            paths.append(p)
        base = datetime(2025, 2, 13, 10)
        cands = [
            self._cand(p, base + timedelta(minutes=i)) for i, p in enumerate(paths)
        ]
        kept = curate_carousel.dedup(cands, threshold=6, out_dir=str(tmp_path))
        assert len(kept) == 3


# ---------------------------------------------------------------------------
# time-burst sampling
# ---------------------------------------------------------------------------


class TestBursts:
    def _kept(self, dts, hashes):
        return [
            (
                {
                    "fn": f"img{i}.jpg",
                    "dt": dt,
                    "path": f"/x/img{i}.jpg",
                    "gps": None,
                    "bytes": 1000,
                },
                h,
            )
            for i, (dt, h) in enumerate(zip(dts, hashes))
        ]

    def test_gap_splits_bursts(self, tmp_path):
        base = datetime(2025, 2, 13, 10)
        dts = [base + timedelta(minutes=m) for m in (0, 10, 20, 180, 190)]
        kept = self._kept(dts, [0x00, 0xFF, 0x0F, 0x00, 0xFF])
        chosen = curate_carousel.bursts(
            kept, gap_min=45, per_burst=4, out_dir=str(tmp_path)
        )
        # 2 bursts; first burst has 3 frames (all kept, per_burst=4), second has 2
        assert len(chosen) == 5

    def test_per_burst_caps_with_mmr_diversity(self, tmp_path):
        base = datetime(2025, 2, 13, 10)
        dts = [base + timedelta(minutes=m) for m in (0, 5, 10)]
        kept = self._kept(dts, [0x00, 0x0F, 0xFF])  # 0xFF most different from 0x00
        chosen = curate_carousel.bursts(
            kept, gap_min=45, per_burst=2, out_dir=str(tmp_path)
        )
        assert len(chosen) == 2
        assert chosen[0][0]["dt"] == dts[0]  # first frame always kept
        assert chosen[1][1] == 0xFF  # MMR picks the diverse one

    def test_output_sorted_by_datetime(self, tmp_path):
        base = datetime(2025, 2, 13, 10)
        dts = [base + timedelta(minutes=m) for m in (0, 10, 200)]
        kept = self._kept(dts, [0x00, 0xFF, 0x0F])
        chosen = curate_carousel.bursts(
            kept, gap_min=45, per_burst=1, out_dir=str(tmp_path)
        )
        got = [c["dt"] for c, _ in chosen]
        assert got == sorted(got)


# ---------------------------------------------------------------------------
# scan date filtering (lightweight, no GPS box)
# ---------------------------------------------------------------------------


class TestScan:
    def test_scan_filters_by_date_and_drops_nodateless(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "IMG_2025-02-13_10-00-00_000.jpg").touch()
        (src / "IMG_2025-02-20_10-00-00_000.jpg").touch()
        (src / "random.jpg").touch()  # no timestamp -> dropped as nodate
        start = datetime(2025, 2, 13)
        end = datetime(2025, 2, 14)
        cands = curate_carousel.scan(
            str(src), start, end, box=None, out_dir=str(tmp_path)
        )
        assert [c["fn"] for c in cands] == ["IMG_2025-02-13_10-00-00_000.jpg"]


# ---------------------------------------------------------------------------
# curate_carousel end-to-end funnel on synthetic images (exercises main(argv))
# ---------------------------------------------------------------------------


class TestCurateMain:
    def test_full_funnel(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        for i, (a, b) in enumerate([(5, 3), (41, 17), (7, 29), (13, 53)]):
            p = src / f"IMG_2025-02-13_1{i}-00-00_000.png"
            _pattern_image(p, a, b)
        out = tmp_path / "review"
        rc = curate_carousel.main(["--source", str(src), "--out-dir", str(out)])
        assert rc == 0
        manifest = json.loads((out / "manifest.json").read_text())
        assert len(manifest) == 4  # 4 distinct patterns survive dedup
        assert sorted((out / "thumbs").glob("*.jpg"))
        assert (out / "sheet_1.jpg").exists()
