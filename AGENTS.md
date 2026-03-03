# Agent Notes: ReelTug

## Project Purpose
ReelTug is a Windows 11 PyQt5 desktop app for editing cine film scan videos.
Primary workflow:
1. Fetch queue of reels to edit from API.
2. Generate start/end preview frames per reel (and per split if film broke during scanning).
3. Operator selects trim points and QC comments in GUI.
4. App renders output via ffmpeg (trim, optional reverse, optional concat, optional audio, optional DVD flows).

## Current Entry Points
- Main app: `run.py`
- UI package:
  - `ui/main_window.py`
  - `ui/queue_window.py`
  - `ui/render_window.py`
  - `ui/workers.py`
  - `ui/resources.py`
- Smoke test: `smoke_test.py`

## Key Modules
- `queue_management.py`: pulls queue items and builds internal reel records.
- `preview_handler.py`: preview extraction and caching (CPU + optional GPU acceleration).
- `render.py`: render pipeline and post-processing.
- `api.py`: API auth/get/post wrapper.
- `config.py`: env-driven configuration.
- `reel_models.py`: typed `ReelBatch` model used when constructing reel payloads.
- `path_utils.py`: split/version path helpers.

## Environment Variables
Use `.env.example` as reference:
- `REELTUG_API_HOST`
- `REELTUG_API_USERNAME`
- `REELTUG_API_PASSWORD`
- `REELTUG_API_TIMEOUT_SECONDS`
- `REELTUG_QUEUE_FETCH_TIMEOUT_SECONDS`
- `REELTUG_TRANSFERRING_DIRECTORY`
- `REELTUG_CINE_EDITING_DIR`
- `REELTUG_TEMP_VIDEO_PROCESSING_DIR`
- `REELTUG_RENDER_LOG_DIR`
- `REELTUG_PICKLE_BACKUP_DIR`
- `REELTUG_MUSIC_AUDIO_DIR`
- `REELTUG_C2D_OUT_DIR`
- `REELTUG_C2D_EXE_DIR`
- `REELTUG_C2D_MENU_DIR`

## Fast Validation
1. Syntax:
   - `python -m py_compile run.py ui\\main_window.py ui\\render_window.py ui\\queue_window.py ui\\workers.py ui\\resources.py`
2. Smoke test:
   - `python smoke_test.py`
3. Optional live API smoke:
   - `python smoke_test.py --live-api`

## Current Refactor Status
- UI split from monolithic `run.py` into `ui/` package.
- Hardcoded API credentials removed; now env-driven.
- Basic thread-safety added via locks around shared queue/render collections.
- Dead scripts and duplicate UI files removed in previous cleanup pass.

## Known Risks / Next Work
- `ui/main_window.py` is still large and mixes UI + business logic.
- Render pipeline in `render.py` is still monolithic.
- Limited automated tests beyond smoke checks.
- Some legacy assumptions remain around Windows/network paths and external tools.

## Guidance for Future Agents
- Keep behavior stable unless explicitly asked to change workflow.
- Prefer incremental refactors with smoke-test coverage.
- Avoid destructive git operations.
- Validate syntax (`py_compile`) after edits.
