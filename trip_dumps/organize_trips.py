"""Move month-root photos into dated trip folders using local metadata.

This stage has no Amazon API contentDate: the pull stage filters true capture dates
before files arrive. Prefer camera filename stamps, then EXIF, then mtime.
"""

import argparse
import json
import re
import shutil
import statistics
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from .cluster_trips import (
    fname_dt,
    geo_split_trips,
    geocode,
    haversine_km,
    pil_exif_dt,
    pil_gps,
    temporal_clusters,
)

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass


PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff"}
TRIP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})_")


def slug(value):
    ascii_text = (
        unicodedata.normalize("NFKD", value or "unknown")
        .encode("ascii", "ignore")
        .decode()
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-") or "unknown"


def read_item(path, date_reader=pil_exif_dt, gps_reader=pil_gps):
    dt = (
        fname_dt(path.name)
        or date_reader(path)
        or datetime.fromtimestamp(path.stat().st_mtime)
    )
    gps = gps_reader(path)
    return {
        "path": path,
        "name": path.name,
        "dt": dt,
        "lat": gps[0] if gps else None,
        "lon": gps[1] if gps else None,
    }


def cluster(items, gap_days=3, geo_split_km=150):
    if not items:
        return []
    return geo_split_trips(
        temporal_clusters(sorted(items, key=lambda it: it["dt"]), gap_days),
        geo_split_km,
    )


def centroid(items):
    points = [(it["lat"], it["lon"]) for it in items if it["lat"] is not None]
    return (
        (
            statistics.median(p[0] for p in points),
            statistics.median(p[1] for p in points),
        )
        if points
        else None
    )


def trip_folder(items, location):
    if location is None:
        return None
    dates = [it["dt"].date() for it in items]
    return f"{min(dates)}_{max(dates)}_{slug(location.get('city'))}-{slug(location.get('country'))}"


def matching_folder(items, center, trips, centers, gap_days, geo_split_km):
    dates = [it["dt"].date() for it in items]
    for folder in trips:
        match = TRIP_RE.match(folder)
        if not match:
            continue
        start, end = (datetime.strptime(s, "%Y-%m-%d").date() for s in match.groups())
        if min(dates) < start - timedelta(days=gap_days) or max(
            dates
        ) > end + timedelta(days=gap_days):
            continue
        old = centers.get(folder)
        if (
            old is not None
            and center is not None
            and haversine_km(old, center) > geo_split_km
        ):
            continue
        return folder
    return None


def organize(
    source,
    work_dir,
    gap_days=3,
    geo_split_km=150,
    dry_run=False,
    delta=False,
    date_reader=pil_exif_dt,
    gps_reader=pil_gps,
    geocoder=geocode,
):
    source, work_dir = Path(source), Path(work_dir)
    if not source.is_dir():
        raise ValueError(f"source directory does not exist: {source}")
    manifest_path, cache_path = (
        work_dir / "manifest.json",
        work_dir / "geocode_cache.json",
    )
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    results = {}
    for month in sorted(source.iterdir()):
        if not month.is_dir() or not re.fullmatch(r"\d{4}-\d{2}", month.name):
            continue
        existing = manifest.get(
            month.name, {"trips": {}, "ungrouped": [], "centroids": {}}
        )
        record = json.loads(json.dumps(existing))
        assigned = set(record["ungrouped"])
        for names in record["trips"].values():
            assigned.update(names)
        paths = [
            p
            for p in sorted(month.iterdir())
            if p.is_file() and p.suffix.lower() in PHOTO_EXTS
        ]
        if delta:
            paths = [p for p in paths if p.name not in assigned]
        else:
            # An existing manifest still protects previously processed root files.
            paths = [p for p in paths if p.name not in assigned]
        moved = 0
        for group in cluster(
            [read_item(p, date_reader, gps_reader) for p in paths],
            gap_days,
            geo_split_km,
        ):
            center = centroid(group)
            if center is None:
                record["ungrouped"].extend(it["name"] for it in group)
                continue
            key = f"{center[0]:.2f},{center[1]:.2f}"
            if key not in cache:
                cache[key] = geocoder(*center, "ai-instagram-organizer trip organizer")
                # Nominatim etiquette: at most one live reverse lookup per second.
                if geocoder is geocode:
                    time.sleep(1.1)
            folder = matching_folder(
                group,
                center,
                record["trips"],
                record["centroids"],
                gap_days,
                geo_split_km,
            )
            if folder is None:
                base = trip_folder(group, cache[key])
                folder, suffix = base, 2
                while folder in record["trips"] or (month / folder).exists():
                    folder, suffix = f"{base}-{suffix}", suffix + 1
                record["trips"][folder] = []
                record["centroids"][folder] = list(center)
            destination = month / folder
            if not dry_run:
                destination.mkdir(exist_ok=True)
            for item in group:
                target = destination / item["name"]
                if target.exists():
                    raise FileExistsError(target)
                if not dry_run:
                    shutil.move(str(item["path"]), str(target))
                record["trips"][folder].append(item["name"])
                moved += 1
        results[month.name] = moved
        manifest[month.name] = record
        if not dry_run:
            work_dir.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, indent=2))
            cache_path.write_text(json.dumps(cache, indent=2))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--gap-days", type=float, default=3)
    parser.add_argument("--geo-split-km", type=float, default=150)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delta", action="store_true")
    args = parser.parse_args(argv)
    for month, count in organize(**vars(args)).items():
        print(f"{month}: {count} files assigned")
    return 0
