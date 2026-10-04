# Trip-Dump Pipeline

How to go from a giant unsorted photo library to Instagram-ready carousel drafts,
in "trip dump" form. Proven end to end in Sep-Oct 2026 on 18,099 photos taken in
2025: download -> trip clustering (19 trips found) -> curation funnel ->
4 published-ready carousel drafts (Telluride, NYC, Big Island, Oahu, 15-17 slides each).

The whole pipeline is token-cheap by design: expensive vision review happens only
on ~150 thumbnails per trip, after a cheap local funnel has discarded the rest.

Scripts: `trip_dumps/cluster_trips.py` (stage 1), `trip_dumps/curate_carousel.py` (stage 2).

## Stage 0: Download with true capture dates

Amazon Photos' `createdDate` is the **upload date**, not the capture date. Querying
by `createdDate` placed 42,720 items in December 2025 and zero in Jan-Nov, because
the library had been uploaded in one bulk batch. The real capture date is
`contentDate`.

Rules:

1. Filter on `contentDate`, never `createdDate`.
2. Expect bulk-upload skew: of 42,920 items sitting in "December 2025" by upload
   date, only 18,301 were truly captured in 2025.
3. Dedupe scan nodes: the scan's headline count included 202 duplicate node entries
   (mostly December) that collapse to single files. Final true total was 18,099
   unique photos. Dedupe by full filename.
4. Verify per month against expected counts after download. A month that is one
   file short is worth one targeted re-fetch before moving on.
5. **Filename-matching bug to avoid:** never `split()` filenames on `_` when node
   IDs contain underscores. One transfer script did this for its "already present"
   bookkeeping and kept re-reporting hundreds of files as "to fetch" on every
   re-run, even though the files were already on disk. Match full filenames
   (URL-decoded) or use a strict regex.

## Stage 1: Cluster photos into trips

`trip_dumps/cluster_trips.py`. Input: a directory tree of photos (e.g. one
subfolder per month). Output: `trip_clusters.json` + a markdown report in the
style of `trip-candidates.md`.

### Date source priority (this ordering matters)

1. **Filename timestamps are PRIMARY.** Camera-generated filenames embed the
   capture stamp, e.g. `..._2025-02-13_09-03-08_000.heic`. They proved consistent
   across the whole library.
2. **EXIF `DateTimeOriginal` is demoted to fallback.** On some files it was
   scrambled and produced impossible location interleaving (same timestamp
   jumping between islands). Trusting it blindly merged and split trips wrongly.
3. **File mtime is the last resort**, for files with neither of the above.

### Clustering rules

- **Temporal:** sort all photos by capture time; a gap of **3+ days** between
  consecutive photos starts a new trip.
- **Geographic split:** within a temporal cluster, a **>150 km jump between
  consecutive photos splits the trip only if the jump is SUSTAINED** - the next
  GPS-bearing photo after the jump must also be >150 km from the pre-jump spot.
  This keeps single bad GPS fixes from splitting a trip.
- **Reverse-geocode once per cluster centroid** (Nominatim). Respect the rate
  limit (~1 req/sec) and send a real `User-Agent` with contact info.
- **Embedded trips need GPS-bucket filters.** Someone who shoots daily at home
  will have short trips merged into home-life stretches. Break them out with
  explicit bounding boxes, e.g. NYC metro = lat 40.4-41.1, lon -74.35 to -73.6
  to isolate ~1,280 NYC photos from a mixed 2,322-photo stretch. Record the box
  in the report so the curation stage can reuse it.

### What the report looks like

Per trip: number, date range, photo count, GPS coverage, reverse-geocoded label,
sample filenames, and a TRAVEL vs home-life verdict. Home-life stretches are
cataloged separately as everyday (not trip dumps). Trips under ~100 photos are
marked short; tiny/transition clusters (layovers, 4-photo gas stops) are skipped.

## Stage 2: Curate a carousel (the funnel)

`trip_dumps/curate_carousel.py`. This is the token-discipline core: each step
shrinks the candidate set by an order of magnitude using cheap local compute,
so the vision model only ever sees ~150 thumbnails.

Real numbers from Oahu (2,354 photos in the trip window):

| Step | Survivors |
|------|-----------|
| Date + GPS-box filter | 2,349 |
| dHash dedup | ~2,000 unique |
| Time-burst sampling | 149 |
| Eye review on contact sheets | 18 pulled at full res |
| Full-res face/sharp/exposure check | 16 final slides |

### Step 1: dHash dedup

- 8x8 dHash per photo, Hamming distance threshold **> 6** to count as distinct.
- Sort by file size descending first; **keep the largest file** per near-duplicate
  cluster (it is usually the highest quality).

### Step 2: time-burst sampling

- Group survivors into bursts: a gap of **45+ minutes** starts a new burst.
- Take up to **4 frames per burst**, chosen by maximal marginal relevance on
  dHash distance (start with the first frame, then repeatedly pick the frame
  farthest from the already-picked set). This guarantees temporal coverage with
  visual diversity.

### Step 3: contact sheets for eye review

- Generate numbered thumbnail grids (e.g. 5 columns) with the manifest index
  printed under each frame.
- A human (or vision model) reviews the sheets and picks finalists by index.
  **Review at full resolution for the finalists** - thumbnails hide faces.
  Multiple strong-looking frames were cut only after full-res review revealed
  visible faces (a Times Square night shot, a night-market band, a chin at the
  edge of a brunch spread).

### Step 4: assemble the draft bundle

Format, per trip, under `options/<trip>/`:

```
options/oahu/
  01.jpg  02.jpg  ...  17.jpg   # ordered slides, original aspect ratios, no re-cropping
  preview_oahu.jpg               # labelled preview grid (same look as the review sheets)
  OPTIONS.md                     # slide order + descriptions, alt text, face/dedup notes,
                                 # cut/added rationale, source filename -> original mapping
```

- Convert HEIC originals to JPG quality 93 at full resolution on assembly.
- Keep originals read-only; drafts are copies.

### Selection rules (non-negotiable)

- **Aesthetics only. No visible faces.** Allowed: back views, silhouettes,
  unrecognizably distant figures. Anything with a recognizable face is cut,
  no matter how strong the frame.
- No facial recognition or identity matching anywhere in the pipeline.
- Sharp, well exposed, hi-res only.
- **Slide order:** 2 hooks first, narrative middle (vary the beats: landscapes,
  streets, food, small character moments), one save-worthy payoff last.

### Caption style

- Ultra-short casual lowercase, e.g. `oahu days`
- One rotating question CTA on the second line, e.g. `island time, yes or yes?`
- No hashtags. Location tag set to the trip location.
- Keywords go in per-slide alt text, not the caption.

### Never-posted-twice registry

Standing rule: only original, never-posted photos go out. Enforced, not just
remembered, with a local SQLite registry keyed by dHash
(`trip_dumps/posted_registry.py`):

```bash
# after a draft bundle is published, register every slide:
python -m trip_dumps mark-posted --draft-dir ./options/oahu \
    --post-url https://www.instagram.com/p/XXXX/

# curate while skipping anything already posted:
python trip_dumps/curate_carousel.py --source ~/photos --out-dir ./review \
    --exclude-posted
```

The DB lives at `~/.ai-instagram-organizer/posted.db` (user-level, never in the
repo); override per-run with `--registry`. Hashes are perceptual, so a photo
can never slip back in under a new filename. First registration wins: re-runs
are idempotent.

## Stage 3: Recut workflow
Creative direction changes after the first draft ("keep it aesthetic, nature only:
sunset, sunrise, beach, food, whales, water"). The recut is mechanical:

1. **Cut by category**, not by frame: list what goes (city skyline, streets, mall,
   battleship...) and remove them all.
2. **Backfill from the alternates** (the candidate set from Stage 2, Step 2 -
   keep it around, don't delete it). Rejected-frame notes from the first pass
   save a second full review.
3. **Event-window scans** for specific asks: when the direction names something
   ("whales"), pull the full photo set for the relevant time window and scan it
   directly. The Oahu whale shots came from pulling all 95 photos in the Feb 26
   09:00-13:00 boat window - two keepers (breach + pectoral fin), one distant
   frame skipped as too weak.
4. Rebuild the bundle in place: renumber slides, regenerate the preview grid,
   rewrite OPTIONS.md with a cut/added rationale section.

## Token discipline

The funnel above is the whole trick. Never feed a trip's full photo set to a
vision model (2,354 photos x ~1.5k tokens each is millions of tokens). The
expensive judgment - "is this frame postable?" - happens on ~150 thumbnails
after dedup and burst sampling have done the cheap work locally. Full-resolution
review is limited to the ~18 finalists.

## Proven results (2025 library)

19 trip dumps found; 4 curated to drafts:

| Trip | Dates | Photos | Draft |
|------|-------|--------|-------|
| Oahu, Hawaii | Feb 13-27 | 2,354 | 17 slides |
| Big Island, Hawaii | Oct 3-12 | ~1,370 | 16 slides |
| Telluride, Colorado (Christmas) | Dec 21-29 | 1,013 | 16 slides |
| NYC / New Jersey | Apr 11-May 16 (embedded) | ~1,280 | 15 slides |

Plus 15 more cataloged trips (Eastern Sierra/Death Valley, White Sands NM road
trip, Skagit/North Cascades, Central Coast CA + Sequoia, Vegas/Grand Canyon
road trip, Bethesda/DC, Houston x2, Boston, LA, Dallas, Bar Harbor/Acadia,
SF Bay, Santa Fe mini-trip, Philadelphia).
