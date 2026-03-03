# Architecture Overview

## End-to-End Workflow
1. Queue fetch:
   - `QueueWindow` starts queue thread.
   - `queue_management.QueueManagement.queue_handler()` calls API and creates reel records.
2. Preprocess queue:
   - `MainWindow` starts preprocess manager thread.
   - `preprocess_handler.PreprocessHandler` prepares working media when `prep_state=TO_PREP`.
   - AVI reels are converted to MOV.
   - Reels with reverse comment (`content_int == 8`) are pre-reversed before editing.
   - For split reels with pre-reverse, source split mapping is inverted into output slots so editor flow appears as `SPN -> ... -> SP1`.
   - For adjacent split pairs, preprocess also computes crossover suggestions and confidence (`split_match_suggestions`).
3. Preview cache:
   - `MainWindow` starts preview manager thread.
   - `preview_handler.PreviewHandler` extracts start/end previews per reel/split.
   - Queue UI keeps reels visible through preview states (not only initial `RECORDED`).
4. Edit interaction:
   - User selects split navigation, trim highlights, QC comments in `MainWindow`.
   - When available, split crossover suggestions are preselected on load for unedited splits.
   - Status bar shows split-match state and confidence (auto suggestion vs operator-adjusted).
   - Highlight/trim/QC data is stored back onto active reel dict.
5. Render queue:
   - Edited reel moved to render queue (`state=TO_RENDER`).
   - Render queue supports moving `TO_RENDER`/`FAILED` reels back into edit mode (`Modify` action).
6. Render:
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
- Preprocess state: `prep_state`, `prep_error`, `pre_reverse_required`, `pre_reversed`
- Split match metadata: `split_match_suggestions[split]` with `suggested_start_frame` / `suggested_end_frame` and confidence keys
- Split-level settings: `reel[split]['reverse']`, `reel[split]['reverse_set_by_operator']`, optional `reel[split]['fps']`
  - Reverse flow uses time reverse + `hflip,vflip` (180-degree correction).
  - For pre-reversed reels, render only applies reverse when `reverse_set_by_operator=True` for the split.
  - If all effective split reverses are enabled and concat is enabled, split concat order is reversed (last split first).

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
