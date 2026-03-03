# Agent Start Here

Use this checklist at the start of a new coding-agent session.

## 1) Read Context (in order)
1. `AGENTS.md`
2. `README.md`
3. `docs/ARCHITECTURE.md`
4. `docs/CHANGELOG_FOR_AGENTS.md`

## 2) Confirm Current State
1. Inspect workspace changes:
   - `git status --short`
2. Verify syntax:
   - `python -m py_compile run.py ui\main_window.py ui\render_window.py ui\queue_window.py ui\workers.py ui\resources.py`
3. Run smoke checks:
   - `python smoke_test.py`
   - includes pre-reverse split remap verification (`split_remap`)

Optional (if credentials are configured):
- `python smoke_test.py --live-api`

## 3) Environment Prereqs
- Windows 11 target environment.
- ffmpeg/ffprobe available to app runtime.
- VLC runtime installed (for `python-vlc` playback).
- API env vars set when doing live queue/API tasks:
  - `REELTUG_API_USERNAME`
  - `REELTUG_API_PASSWORD`

Use `.env.example` for full variable list.

## 4) Current Architecture Landmarks
- App bootstrap: `run.py`
- Main UI logic: `ui/main_window.py`
- Queue window: `ui/queue_window.py`
- Render window: `ui/render_window.py`
- Worker wrappers/signals: `ui/workers.py`
- Queue ingestion: `queue_management.py`
- Preview caching: `preview_handler.py`
- Render pipeline: `render.py`

## 5) Work Safely
- Prefer incremental changes + smoke tests.
- Keep behavior stable unless explicitly asked to change workflow.
- Avoid destructive git operations.
- Update `docs/CHANGELOG_FOR_AGENTS.md` after major refactor steps.
