from datetime import date

from trip_dumps.amazon_photos_pull import (
    destination,
    local_ids,
    month_windows,
    selected_nodes,
    taken_date,
)


def node(node_id, name, content_date=""):
    return {
        "id": node_id,
        "name": name,
        "contentProperties": {"contentDate": content_date, "extension": "jpg"},
    }


def test_capture_date_precedence_and_unknown():
    rows = [
        node("a" * 22, "a_2026-01-01_00-00-00.jpg", "2026-02-03T10:00:00Z"),
        node("b" * 22, "b_2026-03-04_10-00-00.jpg"),
        node("c" * 22, "unmarked.jpg"),
    ]
    found, unknown = selected_nodes(rows, date(2026, 2, 1), date(2026, 3, 31))
    assert [x["taken"] for x in found] == ["2026-02-03", "2026-03-04"]
    assert unknown == 1
    assert taken_date({"contentDate": "", "name": "unmarked.jpg"}) is None


def test_bucket_windows():
    assert list(month_windows(date(2026, 1, 20), date(2026, 3, 5))) == [
        (date(2026, 1, 20), date(2026, 2, 1)),
        (date(2026, 2, 1), date(2026, 3, 1)),
        (date(2026, 3, 1), date(2026, 3, 5)),
    ]


def test_dedup_and_filename_collision(tmp_path):
    first_id = "a_b" + "x" * 19
    folder = tmp_path / "2026-02"
    folder.mkdir()
    (folder / f"{first_id}_old.jpg").write_bytes(b"fake")
    assert first_id in local_ids(tmp_path)
    info = {"id": "z" * 22, "extension": "jpg"}
    occupied = set()
    first = destination(tmp_path, info, date(2026, 2, 3), occupied)
    second = destination(tmp_path, info, date(2026, 2, 3), occupied)
    assert first != second
    assert second.name.endswith("-2.jpg")
    rows = [
        node(first_id, "x_2026-02-03_00-00-00.jpg"),
        node(first_id, "x_2026-02-03_00-00-00.jpg"),
    ]
    found, _ = selected_nodes(rows, date(2026, 1, 1), date(2026, 12, 31))
    assert len(found) == 1
