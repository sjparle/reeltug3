# Architecture Overview

## End-to-End Workflow
1. Queue fetch:
   - `QueueWindow` starts queue thread.
   - `queue_management.QueueManagement.queue_handler()` calls API and creates reel records.
2. Preview cache:
   - `MainWindow` starts preview manager thread.
   - `preview_handler.PreviewHandler` extracts start/end previews per reel/split.
   - Queue UI keeps reels visible through preview states (not only initial `RECORDED`).
3. Edit interaction:
   - User selects split navigation, trim highlights, QC comments in `MainWindow`.
   - Preview strips support small-step (`<` / `>`) and 5-step (`<<` / `>>`) paging.
   - Highlight/trim/QC data is stored back onto active reel dict.
4. Render queue:
   - Edited reel moved to render queue (`state=TO_RENDER`).
   - Render queue supports moving `TO_RENDER`/`FAILED` reels back into edit mode (`Modify` action).
5. Render:
   - `RenderWindow` starts render thread.
   - `render.ProcessVideo` orchestrates staged render steps (context load, split/single pipeline, audio/finalization, publish, cleanup).
   - While blocked on ConvertX, reel state is `WAITING_FOR_CONVERTX`.

## Main Data Shape (Reel)
Queue ingestion builds reels using `reel_models.ReelBatch`, then stores as dict for compatibility.
Common fields used across app:
- Identity: `id`, `order_number`, `item_number`
- Source: `video_dir`, `video_name`, `file_type`, `splits`
- Options: `add_music`, `concat`, `increase_fps`, `single_dvd`, `multi_dvd`
- Content metadata: `film_type`, `title`, `subtitle`, `qc_data`
- Runtime/edit state: `state`, `preview_loaded`, `preview_data`, `trim_data`, `highlight_data`
- Split-level settings: `reel[split]['reverse']`, optional `reel[split]['fps']`
  - Reverse flow currently applies time reverse + `hflip,vflip` (180-degree correction).
  - If all splits are reversed and concat is enabled, split concat order is reversed (last split first).

## Concurrency Model
- UI and worker threads share mutable lists:
  - `mainwindow.queue_batches`
  - `mainwindow.render_batches`
- Current protection:
  - `mainwindow.queue_lock` (`threading.RLock`)
  - `mainwindow.render_lock` (`threading.RLock`)

## UI Layer Split
- `ui/main_window.py`: main editing UI + settings window.
- `ui/queue_window.py`: queue window, queue refresh thread.
- `ui/render_window.py`: render queue window, render thread management.
- `ui/workers.py`: QRunnable wrappers and signal container.
- `ui/resources.py`: resolved paths for ui files and ffmpeg binaries.

## External Dependencies
- API service (auth + queue + updates): configured in `config.py` / env vars.
- ffmpeg/ffprobe binaries: expected near app runtime path.
- VLC runtime for playback in PyQt frame.
- Optional GPU preview acceleration: torch/torchvision/GPUtil.

## Known Technical Debt
- `ui/main_window.py` still mixes view logic and domain logic.
- `render.py` still has shared mutable state; orchestration has been split into stage helpers but command building and domain rules are not fully isolated yet.
- No robust unit/integration test suite yet; smoke tests only.
