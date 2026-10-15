# Carousel Curation Playbook

Generic, tool-agnostic lessons for curating photo carousels with an AI
assistant without burning tokens or reposting. Pairs with
`docs/TRIP_DUMP_PIPELINE.md` (the mechanics) — this file is the *judgment*.

## 1. Token-efficient pipeline

Full-resolution image reads are the dominant token cost in curation. Never
point the agent at a folder of originals. The shape that works:

1. **Scripted screening, where the photos live.** A script (not the agent)
   runs face detection, perceptual-hash dedup against the posted registry
   and the current draft, sharpness scoring (Laplacian variance), and
   exposure-clipping rejection. In practice this screens a ~3,400-file trip
   in about 11 minutes of compute with zero vision tokens.
2. **Agent sees thumbnails only.** The script emits small thumbnails
   (~400px) plus a manifest with per-file scores. The agent picks finalists
   and their narrative order from thumbnails alone.
3. **Assemble chosen originals.** Only the selected files are pulled at full
   resolution, cropped to the target aspect ratio, and bundled. See
   `trip-dump fast-curate --assemble`.

Rule of thumb: if the agent is opening more than ~30 images for a carousel,
the funnel is leaking — push more screening into the script.

## 2. Automated face detection is a prefilter, not a gate

OpenCV Haar cascades measured ~96% false-positive rate on architecture-heavy
travel photos (rejecting good frames for window patterns and statues) while
still letting some real faces through. Treat detector output as a triage
signal only: every keeper gets a human look at full resolution before it
ships. Never use facial recognition or identity matching — detection answers
"is there a face?", never "whose face?".

## 3. Curation rules

- **Hook first.** The strongest single frame opens the carousel; it has to
  stop thumbs with no context.
- **Payoff last.** The final slide should be genuinely save-worthy (a wide,
  a reveal, a payoff) — saves are the metric that compounds.
- **Alternate grand and intimate.** Wide landscapes interleaved with detail
  shots (facades, textures, food, signage) hold swipes across 18–20 slides;
  long runs of same-scale frames bleed viewers.
- **Dedup against everything.** Check candidates against the posted registry
  *and* the current draft (perceptual hash, Hamming ≤ 6). Crops defeat naive
  hashing, so record each published slide's *source-file* hash at build time
  (`trip-dump mark-posted` handles this via `ASSEMBLY.json`).
- **Two-line caption, rotating question.** Line one names the trip; line two
  is a question that pits two halves of the set against each other
  ("old town or river bends?"). Observed: question captions drew roughly 3x
  the comments of statement captions. Observed, not guaranteed — rotate the
  question per post and never reuse one from a recent post.
- **One idea per carousel.** A tight single-trip set outperforms a
  greatest-hits mix; split mixed shoots into separate posts.

## 4. Tooling map

| Step | Command |
|---|---|
| Pull from photo library | `trip-dump pull` (capture-date filtered) |
| Month → trip folders | `trip-dump organize` (metadata-only) |
| Cluster a trip | `trip-dump cluster` |
| Screen + thumbnail shortlist | `trip-dump fast-curate` |
| Assemble final slides | `trip-dump fast-curate --assemble` |
| Register published slides | `trip-dump mark-posted` |
