# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.2.0] - 2026-10-04

### Added
- Trip-dump pipeline (`a8f16f1`): `trip_dumps/cluster_trips.py` clusters photos
  into trips by true capture date + GPS; `trip_dumps/curate_carousel.py` runs the
  token-cheap curation funnel (dHash dedup, time-burst sampling, contact sheets,
  draft-bundle assembly). Full playbook in `docs/TRIP_DUMP_PIPELINE.md`.
- `AGENTS.md`: agent operating guide for the repo.
- `skills/instagram-trip-dumps/SKILL.md`: installable agent skill (Claude Code,
  Codex, OpenClaw, Hermes, ChatGPT) that teaches the trip-dump workflow.
- Never-posted-twice registry: `trip_dumps/posted_registry.py` (SQLite, dHash
  keyed, user-level `~/.ai-instagram-organizer/posted.db`); `curate` gains
  `--exclude-posted`/`--registry`; new `trip-dump mark-posted` step registers a
  published draft bundle.
- `reels/assemble.py`: minimal real-motion clip assembler (trim + concat +
  optional audio via ffmpeg; still-image slideshows explicitly out of scope).
- Offline pytest suite (`tests/test_offline_*.py`, 60+ tests) and GitHub Actions
  CI (tests + ruff + black).
- `.pre-commit-config.yaml` (ruff + black hooks).
- `requirements-optional.txt`: heavy analytics deps split out of core install.

### Changed
- `ai_instagram_organizer.py` (3,458-line god-file) split into the `organizer/`
  package (`config`, `ratelimit`, `providers/{gemini,llama,ollama}`, `images`,
  `analysis`, `cli`, `common`); thin backwards-compatible shim kept. CLI
  `--help` output unchanged.
- `GeminiRateLimiter`/`LlamaRateLimiter` unified into one shared `RateLimiter`
  base class (`organizer/ratelimit.py`); no behavioral change.
- New `trip-dump` CLI subcommand (`cluster` / `curate` / `mark-posted`) and
  `python -m trip_dumps`.
- `docs/` consolidated: overlapping Gemini/Llama/batch guides merged into
  `GEMINI_GUIDE.md`, `LLAMA_GUIDE.md`, `PERFORMANCE_GUIDE.md`.
- README: new Trip-Dump Pipeline section, "Use with AI assistants" skill-install
  guide, core-vs-optional install instructions.

### Fixed
- Removed a duplicate dead definition of `analyze_single_image_gemini_direct`
  that shadowed the real implementation.
- `docs/CONTEXTUAL_FILTERING_GUIDE.md`: removed a roadmap item proposing face
  recognition, per the repo's no-facial-recognition rule.
