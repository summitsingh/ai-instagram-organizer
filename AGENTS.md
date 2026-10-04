# AGENTS.md

Agent operating guide for `ai-instagram-organizer`. Read this when you land in
the repo and need to be productive.

## What this repo is

An AI-powered toolkit that turns unsorted personal photo libraries into
Instagram-ready carousel posts ("trip dumps"). The flagship workflow is the
**trip-dump pipeline**: filter photos by true capture date -> cluster them into
trips by date + GPS -> curate a carousel per trip through a token-cheap funnel ->
produce a reviewable draft bundle.

## The pipeline in brief

1. **True-capture-date filtering.** Amazon Photos `createdDate` is the *upload*
   date; `contentDate` is the capture date. Bulk uploads make naive queries
   useless (42,920 items "in December 2025", only 18,301 truly taken in 2025).
   Dedupe scan nodes by full filename (never `split()` on `_`: node IDs contain
   underscores).
2. **Trip clustering** (`trip_dumps/cluster_trips.py`). Filename timestamps are
   the PRIMARY date source (`_2025-02-13_09-03-08_000` pattern); EXIF
   DateTimeOriginal is fallback (it was scrambled on some files); mtime is last
   resort. Rules: 3+ day photo gap = new trip; split on SUSTAINED >150 km
   location jumps (one bad GPS fix must not split a trip); one Nominatim
   reverse-geocode per cluster centroid (~1 req/sec, real User-Agent).
   Embedded trips (short trips inside home-life stretches) need explicit GPS
   bounding boxes, e.g. NYC = lat 40.4-41.1, lon -74.35 to -73.6.
3. **Curation funnel** (`trip_dumps/curate_carousel.py`). Cheap local steps first:
   dHash dedup (8x8, Hamming > 6, keep largest file) -> time-burst sampling
   (45-min gaps, up to 4 diverse frames per burst via maximal marginal relevance)
   -> thumbnails + manifest -> numbered contact sheets for eye review. Vision
   review happens ONLY on ~150 thumbnails; full-resolution pull of ~18
   finalists; every finalist checked at FULL RES for faces, sharpness, exposure
   (thumbnails hide faces).
4. **Draft bundle.** `options/<trip>/01.jpg...N.jpg` (ordered, original crops,
   HEIC -> JPG q93), a labelled `preview_<trip>.jpg` grid, and `OPTIONS.md`
   (slide order + descriptions, alt text, face/dedup notes, cut/added rationale,
   source-filename mapping). Captions: ultra-short casual lowercase + one
   rotating question CTA, no hashtags, location tag set.

Full playbook: `docs/TRIP_DUMP_PIPELINE.md`. Installable skill version:
`skills/instagram-trip-dumps/SKILL.md`.

## Running the scripts

```bash
pip install pillow requests          # pillow-heif too, if HEIC sources exist

# 1. Cluster into trips
python trip_dumps/cluster_trips.py --source ~/photos/2025 \
    --out-json trip_clusters.json --out-md trip-candidates.md --geocode

# 2. Funnel one trip (date window + GPS box for embedded trips)
python trip_dumps/curate_carousel.py --source ~/photos/2025 \
    --out-dir ./oahu-review --start 2025-02-13 --end 2025-02-28 \
    --gps-box 21.2,21.8,-158.35,-157.6
# -> review sheet_*.jpg, note finalist manifest indexes

# 3. Assemble the ordered draft bundle from full-res originals
python trip_dumps/curate_carousel.py --out-dir ./oahu-review \
    --assemble "12,11,33,48" --draft-dir ./options/oahu

# 4. After publishing, register the bundle so it is never picked again
python -m trip_dumps mark-posted --draft-dir ./options/oahu \
    --post-url https://www.instagram.com/p/XXXX/
# (curate with --exclude-posted to skip registered photos on future runs)
```

## Reels

`reels/assemble.py` trims and concatenates real-motion clips into one vertical
reel (ffmpeg, optional system dep). Still-image slideshows are out of scope by
design.

```bash
python reels/assemble.py --clip intro.mp4:3.0 --clip main.mp4 \
    --clip outro.mp4:2.5 --audio track.mp3 --out reel.mp4
```

## Conventions

- **Never commit** photos, credentials, API keys, `__pycache__/`,
  `temp_converted_images_*`, `instagram_posts_*/`, or any `*-work/` review dirs.
  Draft bundles under `options/` are working artifacts, not repo content.
- Keep new code **dependency-light**: stdlib + `pillow` + `requests` + optional
  `pillow-heif`. No new cloud SDKs without a reason.
- `docs/` holds the playbooks; each new workflow gets a doc, not just a script.
- Face rule is absolute everywhere: no visible faces in any curated output
  (back views, silhouettes, unrecognizably distant figures only). No facial
  recognition, ever.
- The rest of the repo (Llama/Gemini/Ollama providers, hashtag intelligence,
  scheduling) predates this pipeline; the trip-dump scripts are standalone and
  do not depend on it.
