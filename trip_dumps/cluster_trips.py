#!/usr/bin/env python3
"""Cluster a photo library into trip dumps by capture date and GPS location.

Method (proven on 18,099 photos in Sep-Oct 2026):
  1. Date per photo: filename timestamp FIRST (e.g. `_2025-02-13_09-03-08_000`
     embedded in camera-generated names), EXIF DateTimeOriginal as fallback
     (it was scrambled on some files), file mtime as last resort.
  2. Sort by capture time; a gap of >= --gap-days starts a new trip.
  3. Split a temporal cluster on a SUSTAINED geographic jump of >= --geo-split-km
     between consecutive photos (the next GPS-bearing photo must also be far
     from the pre-jump spot, so one-off bad GPS fixes don't split a trip).
  4. Optionally reverse-geocode one centroid per trip (Nominatim, ~1 req/sec).

Output: trip_clusters.json plus a markdown report (trip-candidates style).

Dependencies: stdlib only, plus Pillow (for EXIF GPS; graceful degrade without
it) and requests (only needed for --geocode).
"""
import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta

try:
    from PIL import Image
    from PIL.ExifTags import IFD
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

# Camera filename stamp: _YYYY-MM-DD_HH-MM-SS_mmm
FNAME_DT = re.compile(r"_(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})(?:_\d+)?[.\-]")


def fname_dt(name):
    """Capture time from the camera filename stamp (primary date source)."""
    m = FNAME_DT.search(name)
    if m:
        try:
            return datetime(*(int(x) for x in m.groups()))
        except ValueError:
            pass
    return None


def _rational_to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        # IFDRational or (num, den) tuple
        try:
            return float(v.numerator) / float(v.denominator)
        except (AttributeError, ZeroDivisionError):
            return float(v[0]) / float(v[1])


def pil_gps(path):
    """GPS (lat, lon) from EXIF via Pillow. Returns None if absent/unreadable."""
    if not HAVE_PIL:
        return None
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return None
            gps = exif.get_ifd(IFD.GPSInfo)
            if not gps:
                return None
            lat_v, lat_r = gps.get(2), gps.get(1)
            lon_v, lon_r = gps.get(4), gps.get(3)
            if lat_v is None or lon_v is None:
                return None
            lat = sum(_rational_to_float(x) / (60 ** i) for i, x in enumerate(lat_v))
            lon = sum(_rational_to_float(x) / (60 ** i) for i, x in enumerate(lon_v))
            if str(lat_r) == "S":
                lat = -lat
            if str(lon_r) == "W":
                lon = -lon
            return (lat, lon)
    except Exception:
        return None


def pil_exif_dt(path):
    """EXIF DateTimeOriginal via Pillow (fallback date source)."""
    if not HAVE_PIL:
        return None
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return None
            tag = exif.get(36867)  # DateTimeOriginal
            if tag:
                try:
                    return datetime.strptime(str(tag), "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    pass
    except Exception:
        pass
    return None


def haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(h))


def geocode(lat, lon, user_agent):
    """Reverse-geocode one centroid. Nominatim: ~1 req/sec, real User-Agent."""
    import requests
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 10},
            headers={"User-Agent": user_agent},
            timeout=30,
        )
        r.raise_for_status()
        addr = r.json().get("address", {})
        city = (addr.get("city") or addr.get("town") or addr.get("village")
                or addr.get("county"))
        state = addr.get("state")
        country = addr.get("country")
        return {"city": city, "state": state, "country": country,
                "label": ", ".join(x for x in (city, state, country) if x)}
    except Exception as e:
        return {"city": None, "state": None, "country": None,
                "label": f"geocode failed: {e}"}


def iter_photos(source):
    exts = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff"}
    for root, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for fn in sorted(files):
            if os.path.splitext(fn)[1].lower() not in exts:
                continue
            yield os.path.join(root, fn), fn


def parse_box(s):
    """lat0,lat1,lon0,lon1 bounding box for embedded-trip isolation."""
    parts = [float(x) for x in s.split(",")]
    if len(parts) != 4:
        raise ValueError("box must be lat0,lat1,lon0,lon1")
    return tuple(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, help="photo directory tree")
    ap.add_argument("--out-json", default="trip_clusters.json")
    ap.add_argument("--out-md", default="trip-candidates.md")
    ap.add_argument("--gap-days", type=float, default=3.0,
                    help="photo gap that starts a new trip (default 3)")
    ap.add_argument("--geo-split-km", type=float, default=150.0,
                    help="sustained location jump that splits a trip (default 150)")
    ap.add_argument("--gps-box", default=None,
                    help="optional lat0,lat1,lon0,lon1 box to pre-isolate a region")
    ap.add_argument("--geocode", action="store_true",
                    help="reverse-geocode one centroid per trip (Nominatim)")
    ap.add_argument("--user-agent", default="ai-instagram-organizer/1.0",
                    help="User-Agent for Nominatim (include contact)")
    ap.add_argument("--min-photos", type=int, default=1,
                    help="drop clusters smaller than this from the report")
    args = ap.parse_args()

    box = parse_box(args.gps_box) if args.gps_box else None

    items = []
    for path, fn in iter_photos(args.source):
        # Date priority: filename stamp -> EXIF DateTimeOriginal -> mtime.
        # EXIF was scrambled on some files (impossible location interleaving),
        # so the filename stamp is the primary source.
        dt = fname_dt(fn) or pil_exif_dt(path)
        if dt is None:
            dt = datetime.fromtimestamp(os.path.getmtime(path))
        latlon = pil_gps(path)
        if box and latlon:
            lat0, lat1, lon0, lon1 = box
            if not (lat0 <= latlon[0] <= lat1 and lon0 <= latlon[1] <= lon1):
                continue
        items.append({"path": path, "file": fn, "dt": dt,
                      "lat": latlon[0] if latlon else None,
                      "lon": latlon[1] if latlon else None})

    if not items:
        print("no photos found", file=sys.stderr)
        return 1
    items.sort(key=lambda x: x["dt"])
    print(f"photos with dates: {len(items)}", flush=True)

    # Temporal clustering.
    clusters, cur = [], [items[0]]
    gap = timedelta(days=args.gap_days)
    for it in items[1:]:
        if (it["dt"] - cur[-1]["dt"]) >= gap:
            clusters.append(cur)
            cur = [it]
        else:
            cur.append(it)
    clusters.append(cur)

    # Geographic split: sustained jumps only.
    trips = []
    for cl in clusters:
        sub, cur_sub = [], [cl[0]]
        i = 0
        while i < len(cl) - 1:
            it = cl[i + 1]
            prev = cur_sub[-1]
            split = False
            if prev["lat"] is not None and it["lat"] is not None:
                if haversine_km((prev["lat"], prev["lon"]),
                                (it["lat"], it["lon"])) > args.geo_split_km:
                    nxt = next((c for c in cl[i + 2:] if c["lat"] is not None), None)
                    if nxt is None or haversine_km(
                            (prev["lat"], prev["lon"]),
                            (nxt["lat"], nxt["lon"])) > args.geo_split_km:
                        split = True
            if split:
                sub.append(cur_sub)
                cur_sub = [it]
            else:
                cur_sub.append(it)
            i += 1
        sub.append(cur_sub)
        trips.extend(sub)
    print(f"raw trip clusters: {len(trips)}", flush=True)

    results = []
    for i, t in enumerate(trips):
        if len(t) < args.min_photos:
            continue
        gps = [(x["lat"], x["lon"]) for x in t if x["lat"] is not None]
        centroid = ((sum(g[0] for g in gps) / len(gps),
                     sum(g[1] for g in gps) / len(gps)) if gps else (None, None))
        geo = None
        if args.geocode and centroid[0] is not None:
            geo = geocode(*centroid, args.user_agent)
            time.sleep(1.1)  # Nominatim rate limit
        n = len(t)
        results.append({
            "trip": len(results) + 1,
            "start": t[0]["dt"].strftime("%Y-%m-%d"),
            "end": t[-1]["dt"].strftime("%Y-%m-%d"),
            "count": n,
            "gps_coverage": len(gps),
            "centroid": [round(c, 4) if c is not None else None for c in centroid],
            "geo": geo,
            "samples": ([x["file"] for x in t[:3]]
                        + ([t[n // 2]["file"]] if n > 4 else [])
                        + ([t[-1]["file"]] if n > 1 else [])),
        })
        label = geo["label"] if geo else "no-gps"
        print(f"trip {results[-1]['trip']}: {results[-1]['start']}.."
              f"{results[-1]['end']} n={n} gps={len(gps)} {label}", flush=True)

    with open(args.out_json, "w") as f:
        json.dump(results, f, indent=1)

    with open(args.out_md, "w") as f:
        f.write("# Trip Dump Candidates\n\n")
        f.write(f"Source: {args.source} ({len(items)} photos)\n\n")
        f.write("| # | Trip | Dates | Photos | GPS |\n")
        f.write("|---|------|-------|--------|-----|\n")
        for r in results:
            label = r["geo"]["label"] if r["geo"] else "no-gps"
            f.write(f"| {r['trip']} | {label} | {r['start']}..{r['end']} "
                    f"| {r['count']} | {r['gps_coverage']} |\n")
        f.write("\n## Details\n\n")
        for r in results:
            f.write(f"### {r['trip']}. {r['geo']['label'] if r['geo'] else 'no-gps'} "
                    f"- {r['start']}..{r['end']} ({r['count']} photos)\n")
            for s in r["samples"]:
                f.write(f"- `{s}`\n")
            f.write("\n")
    print(f"wrote {args.out_json} and {args.out_md}")


if __name__ == "__main__":
    sys.exit(main())
