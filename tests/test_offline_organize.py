from datetime import datetime

from trip_dumps.organize_trips import cluster, organize, slug


def test_slug_and_sustained_cluster():
    assert slug("São José, Brazil") == "sao-jose-brazil"
    items = [
        {"dt": datetime(2026, 1, d), "lat": lat, "lon": 0}
        for d, lat in [(1, 0), (2, 0), (3, 10), (4, 10)]
    ]
    assert [len(group) for group in cluster(items)] == [2, 2]


def test_delta_and_manifest(tmp_path):
    source, work = tmp_path / "source", tmp_path / "work"
    month = source / "2026-01"
    month.mkdir(parents=True)
    first = month / "node_id_with_under_scores_2026-01-02_10-00-00_000.jpg"
    first.write_bytes(b"fake")
    calls = []

    def geo(*args):
        calls.append(args)
        return {"city": "São José", "country": "Brazil"}

    options = dict(
        source=source,
        work_dir=work,
        date_reader=lambda _: None,
        gps_reader=lambda _: (1.0, 2.0),
        geocoder=geo,
    )
    assert organize(**options, dry_run=True) == {"2026-01": 1}
    assert first.exists() and not work.exists()
    assert organize(**options) == {"2026-01": 1}
    folder = month / "2026-01-02_2026-01-02_sao-jose-brazil"
    assert (folder / first.name).exists()
    second = month / "other_id_2026-01-03_10-00-00_000.jpg"
    second.write_bytes(b"fake")
    assert organize(**options, delta=True) == {"2026-01": 1}
    assert (folder / second.name).exists()
    assert len(calls) == 2
    assert organize(**options, delta=True) == {"2026-01": 0}


def test_no_gps_stays_in_root(tmp_path):
    month = tmp_path / "source" / "2026-02"
    month.mkdir(parents=True)
    photo = month / "x_2026-02-01_10-00-00_000.jpg"
    photo.write_bytes(b"fake")
    assert organize(
        month.parent,
        tmp_path / "work",
        date_reader=lambda _: None,
        gps_reader=lambda _: None,
        geocoder=lambda *_: None,
    ) == {"2026-02": 0}
    assert photo.exists()
