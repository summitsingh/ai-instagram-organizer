---
name: instagram-trip-dumps
description: Turn an unsorted photo library into Instagram carousel "trip dump" drafts - cluster photos into trips by capture date and GPS, then curate face-free carousels with captions. Use when the user wants trip-based Instagram carousels from their photo library.
---

# Instagram Trip Dumps

You turn a big unsorted photo library into reviewable Instagram carousel drafts,
one per trip. The workflow is a cheap-local-first funnel: only ~150 thumbnails
per trip ever reach vision review, never the full photo set.

The repo this skill ships with provides the tooling:
- `trip_dumps/cluster_trips.py` - cluster photos into trips by capture date + GPS
- `trip_dumps/curate_carousel.py` - dHash dedup, time-burst sampling, contact
  sheets, draft assembly
- `docs/TRIP_DUMP_PIPELINE.md` - the full playbook
- `AGENTS.md` - repo operating guide

If the repo is not checked out, the method below still works - the scripts are
thin wrappers around it.

## Workflow

### 1. Get the photo source and filter by true capture date

Ask the user where the photos are (directory tree, ideally one folder per month).
Warn them: cloud-library "created" dates are often *upload* dates. Filter on the
true capture date:
- Filename timestamps first (camera stamps like `_2025-02-13_09-03-08_000`).
- EXIF DateTimeOriginal second (it can be scrambled on some files - demote it).
- File mtime last resort.
- Dedupe by full filename. Never split filenames on `_` (IDs contain underscores).

### 2. Cluster into trips

Rules: sort by capture time; a 3+ day gap starts a new trip; split on *sustained*
>150 km location jumps (a single bad GPS fix must not split a trip). One
reverse-geocode per cluster centroid (Nominatim, ~1 request/second, real
User-Agent with contact info).

Watch for **embedded trips**: someone who shoots daily at home will have short
trips merged into home-life stretches. Break them out with explicit GPS bounding
boxes (e.g. NYC metro: lat 40.4-41.1, lon -74.35 to -73.6). Report each trip with
date range, photo count, location, and sample filenames, and let the user pick
which trips to curate. Catalog home-life stretches separately as everyday, not
trip dumps.

With the repo scripts:

```bash
python trip_dumps/cluster_trips.py --source ~/photos/2025 \
    --out-json trip_clusters.json --out-md trip-candidates.md --geocode
```

### 3. Curate a carousel (the funnel)

For each chosen trip, run the cheap steps before any vision review:

1. **Filter** to the trip's date window (+ GPS box for embedded trips).
2. **dHash dedup**: 8x8 dHash, Hamming distance > 6 counts as distinct; keep the
   largest file per near-duplicate cluster.
3. **Time-burst sampling**: 45+ minute gaps start a new burst; take up to 4
   diverse frames per burst (maximal marginal relevance on dHash distance).
4. **Contact sheets**: numbered thumbnail grids for review.

```bash
python trip_dumps/curate_carousel.py --source ~/photos/2025 \
    --out-dir ./oahu-review --start 2025-02-13 --end 2025-02-28 \
    --gps-box 21.2,21.8,-158.35,-157.6
```

Then review the sheets (yourself, with vision, or show them to the user) and pick
10-20 finalists. **Pull every finalist at full resolution and check it there** -
thumbnails hide faces.

Selection rules (strict):
- Aesthetics only. **No visible faces.** Back views, silhouettes, and
  unrecognizably distant figures are fine; anything with a recognizable face is
  cut, no matter how strong the frame.
- No facial recognition or identity matching, ever.
- Sharp, well exposed, hi-res; no near-duplicates; original crops preserved.
- Slide order: 2 hooks first, narrative middle with varied beats (landscapes,
  streets, food, small character moments), one save-worthy payoff last.

Assemble the ordered bundle from full-res originals (HEIC -> JPG q93):

```bash
python trip_dumps/curate_carousel.py --out-dir ./oahu-review \
    --assemble "12,11,33,48" --draft-dir ./options/oahu
```

### 4. Draft bundle format

```
options/<trip>/
  01.jpg ... N.jpg      # ordered slides, original aspect ratios
  preview_<trip>.jpg    # labelled preview grid
  OPTIONS.md            # slide order + descriptions, per-slide alt text,
                        # face/dedup notes, cut/added rationale,
                        # source filename -> original mapping
```

### 5. Caption

Ultra-short casual lowercase (e.g. `oahu days`), one rotating question CTA on
the second line (e.g. `island time, yes or yes?`), no hashtags, location tag set
to the trip location. Keywords go in per-slide alt text, not the caption.

### Recuts

If the user changes creative direction after a draft ("nature only: no city
shots"), cut by *category*, backfill from the alternates kept in the review
dir, and re-scan narrow event windows for specific asks (e.g. all photos from a
4-hour whale-watching boat window). Rebuild the bundle in place and record
cut/added rationale in OPTIONS.md.

### Never

- Never publish to Instagram (or anywhere) without the user's explicit approval
  of the exact final post.
- Never commit photos, credentials, or review work dirs to a repo.
- Never feed a trip's full photo set to a vision model. The funnel above is the
  whole point: ~150 thumbnails max after cheap local filtering.
