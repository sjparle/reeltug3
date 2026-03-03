# ReelTug

Windows 11 PyQt5 application for editing cine film scan videos.

## What It Does
1. Loads reel jobs from queue API.
2. Preprocesses working media when needed (AVI->MOV and/or pre-reverse based on QC comments).
3. Generates preview frames from start/end of each working video (including split reels).
4. Lets operator select trim points and QC comments.
5. Renders final outputs with ffmpeg (trim, reverse, concat, audio, DVD-related flows).

Notes:
- Reels with reverse comment (`content_int == 8`) are pre-reversed during preprocess so operators edit right-way-round media.
- For pre-reversed split reels, preprocess remaps split order so editor navigation appears in reverse split order (e.g. `SP3 -> SP2 -> SP1`).
- Post-edit reverse remains available and is only applied for pre-reversed reels when explicitly changed by operator.

## Run
```powershell
python run.py
```

## Build EXE (PyInstaller)
Use the checked-in build script:
```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

Output:
- `dist\ReelTug.exe`

Notes:
- `ffmpeg.exe` and `ffprobe.exe` are included automatically if present in project root.
- UI files and icons are bundled for one-file runtime extraction.
- Build uses `--windowed` (no admin elevation manifest).

## Requirements
Install Python dependencies:
```powershell
pip install -r requirements.txt
```

Core deps in `requirements.txt`:
- PyQt5
- python-vlc
- opencv-python
- requests
- numpy
- natsort
- Pillow
- psutil

## Configuration
Configuration is env-driven via `config.py`.
Use `.env.example` as the source of required variable names.

Minimum required for live queue/API calls:
- `REELTUG_API_USERNAME`
- `REELTUG_API_PASSWORD`

Render debug logs are written per reel to `REELTUG_RENDER_LOG_DIR` (defaults to `<TEMP_VIDEO_PROCESSING_DIR>\render_logs`).
Queue fetch timeout is configurable via `REELTUG_QUEUE_FETCH_TIMEOUT_SECONDS` (default `120`).

## Quick Validation
```powershell
python -m py_compile run.py ui\main_window.py ui\render_window.py ui\queue_window.py ui\workers.py ui\resources.py
python smoke_test.py
```

`smoke_test.py` includes split remap verification for pre-reverse behavior.

Optional live API smoke:
```powershell
python smoke_test.py --live-api
```

## Project Layout
- `run.py`: app bootstrap and palette/window startup.
- `ui/`: GUI windows, worker wrappers, and UI resource paths.
- `queue_management.py`: queue ingestion + reel object creation.
- `preview_handler.py`: preview extraction/caching.
- `render.py`: rendering pipeline.
- `api.py`: API client.
- `config.py`: environment config.
- `reel_models.py`: typed reel dataclass.
- `path_utils.py`: split/version path helpers.

## Agent Handoff
See `AGENTS.md` for concise project context and constraints intended for future coding-agent sessions.
