#!/usr/bin/env python3
"""Token-efficient curation funnel: photo set -> carousel candidates.

Cheap local steps shrink the set by orders of magnitude so vision review
(human or model) only ever sees ~150 thumbnails:

  scan -> date/GPS filter -> dHash dedup -> time-burst sampling ->
  thumbnails + manifest -> numbered contact sheets -> (eye review) ->
  --assemble into an ordered draft bundle.

Proven on Oahu Feb 2025: 2,354 photos -> 149 burst candidates ->
18 full-res pulls -> 16 final slides.

Dependencies: Pillow (required). pillow-heif optional (registers HEIC support
if installed).

Usage:
  python curate_carousel.py --source ~/photos/oahu --out-dir ./oahu-review \\
      --start 2025-02-13 --end 2025-02-28 --gps-box 21.2,21.8,-158.35,-157.6

  # after reviewing contact sheets, assemble finalists by manifest index:
  python curate_carousel.py --assemble "12,11,33,48,10" --out-dir ./oahu-review \\
      --draft-dir ./options/oahu
"""
import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta

from PIL import Image, ImageDraw

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

FNAME_DT = re.compile(r"_(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})(?:_\d+)?[.\-]")
EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff"}


def log(out_dir, *a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    with open(os.path.join(out_dir, "run.log"), "a") as f:
        f.write(msg + "\n")


def fname_dt(name):
    m = FNAME_DT.search(name)
    if m:
        try:
            return datetime(*(int(x) for x in m.groups()))
        except ValueError:
            pass
    return None


def _rat(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return float(v.numerator) / float(v.denominator)
        except (AttributeError, ZeroDivisionError):
            return float(v[0]) / float(v[1])


def gps_of(path):
    """GPS (lat, lon) via Pillow EXIF. None if absent/unreadable."""
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return None
            try:
                from PIL.ExifTags import IFD
                gps = exif.get_ifd(IFD.GPSInfo)
            except Exception:
                return None
            if not gps:
                return None
            lat_v, lat_r = gps.get(2), gps.get(1)
            lon_v, lon_r = gps.get(4), gps.get(3)
            if lat_v is None or lon_v is None:
                return None
            lat = sum(_rat(x) / (60 ** i) for i, x in enumerate(lat_v))
            lon = sum(_rat(x) / (60 ** i) for i, x in enumerate(lon_v))
            if str(lat_r) == "S":
                lat = -lat
            if str(lon_r) == "W":
                lon = -lon
            return (lat, lon)
    except Exception:
        return None


def dhash(path, size=8):
    """8x8 difference hash as int."""
    with Image.open(path) as im:
        g = im.convert("L").resize((size + 1, size), Image.BILINEAR)
        px = list(g.get_flattened_data()) if hasattr(g, "get_flattened_data") \
            else list(g.getdata())  # Pillow < 13 compat
    bits = 0
    for r in range(size):
        for c in range(size):
            bits = (bits << 1) | (
                1 if px[r * (size + 1) + c] > px[r * (size + 1) + c + 1] else 0)
    return bits


def ham(a, b):
    return bin(a ^ b).count("1")


def scan(source, start, end, box, out_dir):
    cands = []
    dropped = {"nodate": 0, "out_of_range": 0, "gps_out": 0, "gps_fail": 0}
    for root, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for fn in sorted(files):
            if os.path.splitext(fn)[1].lower() not in EXTS:
                continue
            dt = fname_dt(fn)
            if dt is None:
                dropped["nodate"] += 1
                continue
            if (start and dt < start) or (end and dt >= end):
                dropped["out_of_range"] += 1
                continue
            path = os.path.join(root, fn)
            gps = gps_of(path) if box else None
            if box:
                lat0, lat1, lon0, lon1 = box
                if gps is None:
                    # No GPS: keep it (don't punish missing EXIF), flag it.
                    dropped["gps_fail"] += 1
                elif not (lat0 <= gps[0] <= lat1 and lon0 <= gps[1] <= lon1):
                    dropped["gps_out"] += 1
                    continue
            cands.append({"fn": fn, "path": path, "dt": dt, "gps": gps,
                          "bytes": os.path.getsize(path)})
    log(out_dir, f"SCAN candidates={len(cands)} dropped={dropped}")
    return cands


def dedup(cands, threshold, out_dir):
    """dHash dedup; keep the largest file per near-duplicate cluster."""
    hashed = []
    for i, c in enumerate(cands):
        try:
            hashed.append((c, dhash(c["path"])))
        except Exception as e:
            log(out_dir, "  dhash fail", c["fn"], str(e)[:80])
        if (i + 1) % 200 == 0:
            log(out_dir, f"  dhash {i+1}/{len(cands)}")
    kept = []
    for c, h in sorted(hashed, key=lambda x: -x[0]["bytes"]):
        if all(ham(h, kh) > threshold for _, kh in kept):
            kept.append((c, h))
    kept.sort(key=lambda x: x[0]["dt"])
    log(out_dir, f"DEDUP unique={len(kept)} (threshold={threshold})")
    return kept


def bursts(kept, gap_min, per_burst, out_dir):
    """Group into time bursts; take up to per_burst diverse frames each."""
    blist, cur = [], []
    for c, h in kept:
        if cur and (c["dt"] - cur[-1][0]["dt"]) > timedelta(minutes=gap_min):
            blist.append(cur)
            cur = []
        cur.append((c, h))
    if cur:
        blist.append(cur)
    chosen = []
    for b in blist:
        sel = [b[0]]
        rest = list(b[1:])
        while len(sel) < min(per_burst, len(b)) and rest:
            best = max(rest, key=lambda x: min(ham(x[1], s[1]) for s in sel))
            sel.append(best)
            rest.remove(best)
        chosen.extend(sel)
    chosen.sort(key=lambda x: x[0]["dt"])
    log(out_dir, f"BURSTS={len(blist)} CHOSEN={len(chosen)}")
    return chosen


def thumbnails(chosen, out_dir, thumb_size):
    th = os.path.join(out_dir, "thumbs")
    os.makedirs(th, exist_ok=True)
    manifest = []
    for i, (c, _) in enumerate(chosen):
        try:
            with Image.open(c["path"]) as im:
                im.thumbnail((thumb_size, thumb_size), Image.LANCZOS)
                im.convert("RGB").save(os.path.join(th, f"{i:04d}.jpg"), quality=80)
            manifest.append({
                "idx": i, "file": c["fn"], "path": c["path"],
                "dt": c["dt"].strftime("%Y-%m-%d %H:%M"),
                "gps": [round(c["gps"][0], 4), round(c["gps"][1], 4)] if c["gps"] else None,
                "bytes": c["bytes"]})
        except Exception as e:
            log(out_dir, "  thumb fail", c["fn"], str(e)[:80])
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    log(out_dir, f"THUMBS_DONE n={len(manifest)}")
    return manifest


def contact_sheets(out_dir, cols=5, rows=4):
    """Numbered grids for eye review. Review THESE, then --assemble finalists."""
    th = os.path.join(out_dir, "thumbs")
    manifest = json.load(open(os.path.join(out_dir, "manifest.json")))
    files = [os.path.join(th, f"{m['idx']:04d}.jpg") for m in manifest]
    per = cols * rows
    cell = 320
    made = 0
    for p in range(math.ceil(len(files) / per)):
        page = files[p * per:(p + 1) * per]
        sheet = Image.new("RGB", (cols * cell, rows * (cell + 28)), "white")
        d = ImageDraw.Draw(sheet)
        for i, fp in enumerate(page):
            try:
                with Image.open(fp) as im:
                    im.thumbnail((cell, cell), Image.LANCZOS)
                    x, y = (i % cols) * cell, (i // cols) * (cell + 28)
                    sheet.paste(im, (x + (cell - im.width) // 2, y))
                    idx = manifest[p * per + i]["idx"]
                    d.text((x + 6, y + cell + 4), f"{idx:04d}", fill="black")
            except Exception:
                pass
        out = os.path.join(out_dir, f"sheet_{p + 1}.jpg")
        sheet.save(out, quality=82)
        made += 1
    log(out_dir, f"SHEETS_DONE n={made} ({cols}x{rows}, {per}/sheet)")
    print(f"Review the sheets in {out_dir}/sheet_*.jpg, note finalist indexes, "
          f"then run with --assemble \"idx,idx,...\"")


def assemble(out_dir, order, draft_dir, quality=93):
    """Copy/convert full-res finalists to an ordered draft bundle (01.jpg...).

    order: comma-separated manifest indexes, e.g. "12,11,33,48".
    IMPORTANT: view every finalist at FULL resolution before assembling -
    thumbnails hide faces.
    """
    manifest = {m["idx"]: m for m in
                json.load(open(os.path.join(out_dir, "manifest.json")))}
    os.makedirs(draft_dir, exist_ok=True)
    idxs = [int(x.strip()) for x in order.split(",") if x.strip() != ""]
    for n, idx in enumerate(idxs, 1):
        m = manifest.get(idx)
        if m is None:
            print(f"  WARN: idx {idx} not in manifest, skipping")
            continue
        with Image.open(m["path"]) as im:
            w, h = im.size
            im.convert("RGB").save(os.path.join(draft_dir, f"{n:02d}.jpg"),
                                   quality=quality)
        print(f"  {n:02d}.jpg <- idx{idx:04d} {m['file']} {w}x{h}")
    print(f"draft bundle in {draft_dir} ({len(idxs)} slides)")


def parse_box(s):
    parts = [float(x) for x in s.split(",")]
    if len(parts) != 4:
        raise ValueError("gps-box must be lat0,lat1,lon0,lon1")
    return tuple(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", help="photo directory tree")
    ap.add_argument("--out-dir", required=True, help="review working dir")
    ap.add_argument("--start", help="YYYY-MM-DD inclusive")
    ap.add_argument("--end", help="YYYY-MM-DD exclusive")
    ap.add_argument("--gps-box", help="lat0,lat1,lon0,lon1 to isolate a region")
    ap.add_argument("--dhash-threshold", type=int, default=6)
    ap.add_argument("--burst-gap-min", type=int, default=45)
    ap.add_argument("--per-burst", type=int, default=4)
    ap.add_argument("--thumb-size", type=int, default=420)
    ap.add_argument("--sheet-cols", type=int, default=5)
    ap.add_argument("--sheet-rows", type=int, default=4)
    ap.add_argument("--assemble", metavar="IDXS",
                    help='comma-separated manifest indexes, e.g. "12,11,33"')
    ap.add_argument("--draft-dir", help="output dir for --assemble")
    ap.add_argument("--quality", type=int, default=93,
                    help="JPEG quality for assembled slides")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    if args.assemble:
        if not args.draft_dir:
            ap.error("--assemble requires --draft-dir")
        assemble(args.out_dir, args.assemble, args.draft_dir, args.quality)
        return 0

    if not args.source:
        ap.error("funnel mode requires --source")
    start = datetime.strptime(args.start, "%Y-%m-%d") if args.start else None
    end = datetime.strptime(args.end, "%Y-%m-%d") if args.end else None
    box = parse_box(args.gps_box) if args.gps_box else None

    cands = scan(args.source, start, end, box, args.out_dir)
    kept = dedup(cands, args.dhash_threshold, args.out_dir)
    chosen = bursts(kept, args.burst_gap_min, args.per_burst, args.out_dir)
    thumbnails(chosen, args.out_dir, args.thumb_size)
    contact_sheets(args.out_dir, args.sheet_cols, args.sheet_rows)
    log(args.out_dir, "ALL_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
