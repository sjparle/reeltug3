# Changelog For Agents

This file tracks high-impact refactor milestones for coding-agent handoff.
Use it as a quick "what changed and why" log before modifying the codebase.

## 2026-03-03

### On-Demand Preview Expansion For Arrow Navigation
- Added lazy preview expansion in `preview_handler.py` for split/start/end navigation.
- Arrow navigation now loads additional frame strips on demand when the requested page extends beyond currently cached preview keys.
- Added explicit `<<` / `>>` navigation behavior to jump 5 preview slots per click (for both start and end strips).
- Stored per-split preview metadata (`video_total_frames`, `video_fps`) to support bounded frame-range checks during lazy loads.
- Reload paths now also ensure required preview ranges are available before restoring saved preview positions.

Why:
- Remove the fixed initial preview-window limit so operators can navigate deeper into long clips/splits when selecting join and trim points.

### Queue/Editor State Recovery + Visibility
- `ui/main_window.py` now sets `fresh_load=True` in `reset_states()` so the app can auto-load the next reel after resets.
- `add_to_render()` now recovers from invalid/empty `active_reel` by loading the next reel instead of hard-failing.
- `ui/queue_window.py` now keeps reels visible in the queue through preview states (`CACHING`/`CACHED`) and hides only render-pipeline states.
- Queue merge now normalizes reel dicts to backfill required keys (including `splits`) to avoid missing-key crashes in long sessions.

Why:
- Fix long-running session state drift where reels disappeared from queue or editing flow got stuck on empty payloads.

### Render Queue Edit-Back Flow
- Added `Modify` button in render queue (`gui_render.ui` + `ui/render_window.py`).
- `Modify` moves selected `TO_RENDER` / `FAILED` reel back to edit mode, opens it in main editor, and removes it from render queue.

Why:
- Allow operators to re-open queued reels for trim/QC changes without manual state surgery.

### Render Wait/Thread Hardening
- Auto-render worker now guards loop errors and uses safe state access (`dict.get`) to avoid silent thread death.
- Added explicit `WAITING_FOR_CONVERTX` render state while blocked on ConvertX completion, and preserved this state during queue refresh merge.
- Moved final `DONE` transition to after ConvertX-related completion flow.

Why:
- Make long render sessions observable and robust when external DVD tooling is active.

### Reverse + Split Concat Fixes
- Added reverse fallback for memory-heavy reels: chunked reverse path with concat reassembly.
- Added duration threshold defaulting to chunked reverse for long clips.
- Reverse now applies both timeline reverse and 180-degree image correction (`hflip,vflip`) for reversed reels.
- Fixed split concat behavior to use each split's final processed output path (not pre-reverse trims).
- Added per-split and concat input logging (`Split N reverse=...`, `Split N final clip path`, `Concat input N`).
- When all splits are reversed and `concat=True`, split concat order now reverses (last split first).

Why:
- Fix cases where logs showed reverse steps ran but final outputs still appeared directionally wrong for split reels.

### DVD Rerender Publish Replace
- DVD publish move now replaces existing destination folders/files instead of failing when rerendering same reel/order.
- Multi-volume destination naming now uses a stable base path per volume.

Why:
- Support repeat edits/rerenders without manual cleanup of existing DVD output folders.

## 2026-02-19

### Render Debug Logging
- Added `REELTUG_RENDER_LOG_DIR` config (default: `<TEMP_VIDEO_PROCESSING_DIR>\render_logs`).
- `render.py` now creates one timestamped `.txt` log per reel render.
- Logs include reel context plus subprocess command execution and exit codes for ffmpeg/ffprobe/ConvertX calls.

Why:
- Improve debugging for reel-specific render failures without relying only on console output.

### Queue Fetch Timeout Hardening
- Added `REELTUG_QUEUE_FETCH_TIMEOUT_SECONDS` config (default: `120`).
- Queue fetch now calls API with longer timeout and one retry for transient read timeouts.

Why:
- Align behavior more closely with legacy app (which effectively had no timeout) and reduce startup queue failures on slow responses.

### Split Audio Preservation
- `render.py` now probes each split input for audio before rendering.
- If any split has audio and `add_music` is disabled:
  - split trims preserve source audio where present.
  - silent splits receive generated silent audio so concat/final outputs keep an audio track.
- Final non-music render now maps optional audio (`-map 0:a?`) and encodes AAC explicitly.

Why:
- Fix cases where split reels with partial audio were rendered to silent outputs.

### Render Pipeline Staging Refactor
- Refactored `render.ProcessVideo.process_video()` into explicit stage helpers:
  - context load
  - processing folder prep
  - split/single pipeline
  - final audio/finalize step
  - output publish
  - completion + cleanup
- Added centralized state transitions via `_set_reel_state()`.

Why:
- Improve readability and reduce regression risk when changing render behavior.

### Sound AVI Sync Transcode Hook
- Queue lookup now falls back to `.avi` when a sound reel has no `.mov` yet.
- Reels carry `has_sound` from queue comment metadata (`content_int == 9`).
- Render pre-processing now auto-runs sync transcode (`.avi` + `Logs` sidecars) to `.mov` before trim/reverse, but only for reels flagged with sound.

Why:
- Allow sound reels scanned to AVI to be normalized into MOV in-app before render stages.

### Continuous Queue Refresh + Preview Backlog
- Queue worker now polls queue API continuously using `REELTUG_API_REFRESH_TIME` interval.
- Queue merge is now idempotent by reel id:
  - adds new reels
  - updates existing reel metadata
  - preserves active local states (editing/rendering/cached states)
  - removes stale passive queue entries
- Preview manager now runs continuously (until app shutdown) and picks up newly discovered reels instead of stopping after a fixed max count.

Why:
- Keep queue current during long-running sessions and automatically generate previews for reels that arrive after app startup.

### Queue-Time AVI Preprocessing
- Added background preprocess worker (`preprocess_handler.py`) that continuously converts queued `.avi` reels to working `.mov` files.
- Queue records now track:
  - `source_video_dir`
  - `working_video_dir`
  - `prep_state` (`TO_PREP`, `PREPARING`, `READY`, `FAILED`)
- Queue UI now shows preprocessing status and blocks loading reels until working media is `READY`.
- Preview generation now runs only for reels with `prep_state=READY`.
- Sound `.avi` reels use DAT/LOG sync transcode when sidecars exist; non-sound reels use standard AVI->MOV mezzanine transcode.

Why:
- Push non-human preprocessing work earlier so reels are edit-ready sooner and avoid heavy conversion during active edit/render steps.

### Sync Transcode Throughput Tuning
- Switched default sync transcode to a faster mode:
  - ProRes profile `2` (Standard) instead of `3` (HQ)
  - `-shortest` default output bound
  - strict frame/time forcing moved behind explicit exact-match mode (test script)
- Avoids unnecessary scale filter when input already matches target dimensions.

Why:
- Prevent very slow or apparently stalled transcodes during queue-time preprocessing and manual sync tests.

### Split Preview Robustness Logging
- Added detailed split preview logs with reel/split identifiers for preview loading.
- `set_previews()` now attempts split-level preview regeneration when `preview_data` is missing/empty.
- Added safe fallback preview frames when video stream metadata is invalid or preview extraction yields no frames.
- Guarded split state creation to avoid `IndexError` on empty preview key lists.

Why:
- Prevent hard crashes when selecting split previews and provide actionable diagnostics for bad/short/missing split media.

### Atomic Preprocess Output Writes
- AVI preprocessing now writes to `*.mov.part` temporary files first.
- Output is ffprobe-validated before promotion to final `*.mov`.
- Existing final MOVs are reused only if they pass validation; invalid files are replaced.

Why:
- Avoid unnoticed corrupt/partial MOV files after app crashes or interrupted preprocess jobs.

### Preprocess Retry + MOV Container Fix
- Added explicit `-f mov` for temp `*.mov.part` outputs so ffmpeg can initialize muxer correctly.
- Queue merge now auto-requeues AVI preprocess when `working_video_dir` is missing and state is `FAILED`/`READY`.

Why:
- Fix preprocess failures caused by `.part` extension format detection and allow safe recovery after manual file cleanup.

### Split-Aware AVI Preprocess
- Queue-time AVI preprocess now transcodes all split source files (`SP1..SPN`) before setting reel to `READY`.
- Preprocess fails explicitly if an expected split source file is missing.
- Queue refresh now validates all expected split working MOV files exist; missing parts auto-reset reel to `TO_PREP`.

Why:
- Prevent split navigation/load crashes caused by only the first split being preprocessed to MOV.

## 2026-02-14

### Project Cleanup (safe delete pass)
- Removed clearly unused/deprecated scripts and duplicate UI assets.
- Kept active runtime path intact (`run.py`, root `.ui`, core modules).
- Goal: reduce noise and accidental edits to dead files.

Verification:
- Core modules still compile with `python -m py_compile ...`.

### Config/API Hardening
- Replaced hardcoded API credentials with env-driven config in `config.py`.
- Updated `api.py` to use:
  - `requests.Session`
  - timeout from config
  - env-configured host/credentials
- Added `.env.example`.

Why:
- Remove secrets from source.
- Make deployments/environment changes safer.

Verification:
- Set `REELTUG_API_USERNAME` / `REELTUG_API_PASSWORD` before live API use.

### Data and Path Refactor Foundations
- Added `reel_models.py` (`ReelBatch` dataclass) for typed reel creation.
- Added `path_utils.py` for split/version path handling.
- Refactored `queue_management.py` to construct reels via `ReelBatch`.
- Removed fragile `locals()` version checks and brittle split substitutions.

Why:
- Stabilize data shape creation and reduce string/regex bugs.

### Thread-Safety Improvements
- Added shared locks on `queue_batches` and `render_batches`.
- Guarded key read/write paths in queue/preview/render windows and handlers.

Why:
- Reduce race conditions between UI and worker threads.

### UI Modularization
- Split monolithic UI code into `ui/` package:
  - `ui/main_window.py`
  - `ui/queue_window.py`
  - `ui/render_window.py`
  - `ui/workers.py`
  - `ui/resources.py`
- Reduced `run.py` to bootstrap-only startup.

Why:
- Easier targeted refactors and lower cognitive load per file.

### Smoke Testing Added
- Added `smoke_test.py`:
  - queue fetch smoke (dry-run default, live optional via `--live-api`)
  - preview generation smoke using temp video
  - render prep smoke
- Render prep smoke gracefully skips if optional deps are missing.

Why:
- Provide fast non-destructive confidence checks after refactors.

Verification:
- `python smoke_test.py`

### Dependency File Rationalization
- Replaced unrealistic `requirements.txt` with runtime-focused dependencies.

Why:
- Align installation surface with what app actually imports.

## Next Recommended Milestones
1. Extract business logic from `ui/main_window.py` into service modules.
2. Break `render.py` into testable pipeline stages.
3. Add deterministic fixture-based tests for reel split handling and trim frame math.
4. Add a CI script that runs `py_compile` + `smoke_test.py` on Windows.
