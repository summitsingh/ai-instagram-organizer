"""Fast, local screening for carousel drafts.

AGENT-SIDE CONTRACT:
1. Run phase 1 on the trip dir (takes minutes).
2. View ONLY thumbs/ plus manifest.json scores. Never open full-res candidates.
3. Pick final indices and their order; run --assemble. Only the chosen full-res
   files are ever pulled.
4. Eyeball thumbnails for faces the cascade can miss (profile/obscured faces);
   the cascade is conservative but not perfect.
5. Caption, question-CTA, and posting-window choices stay agent-side; they come
   from live performance data, not from this script.
"""

import argparse
import json
import os
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat

from .curate_carousel import EXTS, dhash, fname_dt, ham
from .posted_registry import _stored_hash_ints, default_db_path

# Screening limits: low edge energy is soft; clipping above 25% is rejected.
SHARP_MIN = 2.0
CLIP_LIMIT = 0.25
BLACK_LIMIT = 8
WHITE_LIMIT = 247


def load_cv2(required):
    try:
        import cv2

        return cv2
    except ImportError:
        if required:
            raise SystemExit(
                "face screening requires opencv-python (optional dependency)"
            )
        return None


def exposure(image):
    gray = image.convert("L")
    hist = gray.histogram()
    total = gray.width * gray.height
    return {
        "black_pct": sum(hist[:BLACK_LIMIT]) / total,
        "white_pct": sum(hist[WHITE_LIMIT + 1 :]) / total,
    }


def sharpness(image, cv2=None):
    gray = image.convert("L")
    if cv2 is not None:
        import numpy as np

        return float(cv2.Laplacian(np.asarray(gray), cv2.CV_64F).var())
    return float(ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0])


def has_face(image, cv2, cascade):
    image = image.convert("RGB")
    image.thumbnail((800, 800))
    import numpy as np

    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    return (
        len(
            cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
            )
        )
        > 0
    )


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2) + "\n")


def screen(args):
    started = time.monotonic()
    out = Path(args.out_dir)
    thumbs = out / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    log = (out / "run.log").open("w")

    def note(message):
        print(message, flush=True)
        print(message, file=log, flush=True)

    cv2 = load_cv2(not args.no_face_check)
    cascade = None
    if cv2 is not None and not args.no_face_check:
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        if cascade.empty():
            raise SystemExit("OpenCV frontal face cascade could not be loaded")
    registry = args.registry or default_db_path()
    posted = []
    if args.exclude_posted:
        if os.path.isfile(registry):
            posted = _stored_hash_ints(registry)
        else:
            note(f"warning: registry missing: {registry}")
    baseline = []
    if args.baseline_dir:
        for path in sorted(Path(args.baseline_dir).iterdir()):
            if path.is_file() and path.suffix.lower() in EXTS:
                baseline.append(dhash(path))
    config = {
        key: getattr(args, key)
        for key in (
            "source",
            "start",
            "end",
            "top",
            "thumb_px",
            "baseline_dir",
            "registry",
            "exclude_posted",
            "no_face_check",
        )
    }
    counts = {
        key: 0
        for key in (
            "scanned",
            "date_kept",
            "unparseable_date",
            "face_rejected",
            "posted_rejected",
            "baseline_dup_rejected",
            "exposure_rejected",
            "sharp_rejected",
            "shortlisted",
        )
    }
    survivors = []
    for root, dirs, files in os.walk(args.source):
        dirs.sort()
        for name in sorted(files):
            path = Path(root) / name
            if path.suffix.lower() not in EXTS:
                continue
            counts["scanned"] += 1
            date = fname_dt(name)
            if date is None:
                counts["unparseable_date"] += 1
                continue
            date = date.date().isoformat()
            if (args.start and date < args.start) or (args.end and date > args.end):
                continue
            counts["date_kept"] += 1
            try:
                with Image.open(path) as opened:
                    image = ImageOps.exif_transpose(opened).convert("RGB")
                if cascade is not None and has_face(image, cv2, cascade):
                    counts["face_rejected"] += 1
                    continue
                hashed = dhash(path)
                if any(ham(hashed, old) <= 6 for old in posted):
                    counts["posted_rejected"] += 1
                    continue
                if any(ham(hashed, old) <= 6 for old in baseline):
                    counts["baseline_dup_rejected"] += 1
                    continue
                clip = exposure(image)
                if max(clip.values()) > CLIP_LIMIT:
                    counts["exposure_rejected"] += 1
                    continue
                sharp = sharpness(image, cv2)
                if sharp < SHARP_MIN:
                    counts["sharp_rejected"] += 1
                    continue
                survivors.append((path.resolve(), sharp, clip, hashed))
            except (OSError, ValueError) as exc:
                note(f"warning: skipped unreadable image {path}: {exc}")
        if counts["scanned"]:
            note(f"scanned {counts['scanned']} files")
    values = [row[1] for row in survivors]
    low, high = (min(values), max(values)) if values else (0.0, 0.0)
    ranked = []
    for path, sharp, clip, hashed in survivors:
        norm = (sharp - low) / (high - low) if high > low else 1.0
        score = norm - clip["black_pct"] - clip["white_pct"]
        ranked.append((score, path, sharp, clip, hashed))
    ranked.sort(key=lambda row: (-row[0], str(row[1])))
    items = []
    for index, (score, path, sharp, clip, hashed) in enumerate(ranked[: args.top], 1):
        thumb = Path("thumbs") / f"{index:04d}_{path.stem}.jpg"
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((args.thumb_px, args.thumb_px))
            image.save(out / thumb, quality=80)
        items.append(
            {
                "index": index,
                "source_path": str(path),
                "thumb": str(thumb),
                "sharpness": sharp,
                "exposure": clip,
                "dhash": f"{hashed:016x}",
                "score": score,
                "passed_face_check": None if args.no_face_check else True,
                "rejected": False,
            }
        )
    counts["shortlisted"] = len(items)
    report = {**counts, "seconds": time.monotonic() - started, "config": config}
    write_json(out / "manifest.json", {"config": config, "items": items})
    write_json(out / "screening-report.json", report)
    note(f"shortlisted {len(items)} in {report['seconds']:.1f}s")
    log.close()
    return 0


def assemble(args):
    manifest = json.loads((Path(args.out_dir) / "manifest.json").read_text())
    choices = {item["index"]: item for item in manifest["items"]}
    try:
        indices = [int(part.strip()) for part in args.assemble.split(",")]
    except ValueError as exc:
        raise SystemExit(
            "--assemble requires comma-separated manifest indices"
        ) from exc
    if not indices or any(index not in choices for index in indices):
        raise SystemExit("--assemble contains an index absent from manifest.json")
    draft = Path(args.draft_dir)
    draft.mkdir(parents=True, exist_ok=True)
    mapping = []
    sheet = Image.new("RGB", (4 * 270, ((len(indices) + 3) // 4) * 365), "white")
    draw = ImageDraw.Draw(sheet)
    for number, index in enumerate(indices, 1):
        source = choices[index]["source_path"]
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image = ImageOps.fit(image, (1080, 1350), method=Image.Resampling.LANCZOS)
            image.save(draft / f"{number:02d}.jpg", quality=93)
        small = image.resize((250, 313))
        x, y = ((number - 1) % 4) * 270, ((number - 1) // 4) * 365
        sheet.paste(small, (x, y + 30))
        draw.text((x + 5, y + 5), f"{number:02d}", fill="black")
        mapping.append(
            {"slide": number, "manifest_index": index, "source_path": source}
        )
    sheet.save(draft / "preview.jpg", quality=85)
    write_json(draft / "ASSEMBLY.json", {"slides": mapping})
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--thumb-px", type=int, default=400)
    parser.add_argument("--baseline-dir")
    parser.add_argument("--registry")
    parser.add_argument("--exclude-posted", action="store_true")
    parser.add_argument("--no-face-check", action="store_true")
    parser.add_argument("--assemble")
    parser.add_argument("--draft-dir")
    args = parser.parse_args(argv)
    if args.assemble:
        if not args.draft_dir:
            parser.error("--draft-dir is required for assembly")
        return assemble(args)
    if not args.source:
        parser.error("--source is required for screening")
    if args.top < 1 or args.thumb_px < 1:
        parser.error("--top and --thumb-px must be positive")
    return screen(args)


if __name__ == "__main__":
    raise SystemExit(main())
