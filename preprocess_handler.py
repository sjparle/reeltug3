import os
from time import sleep

from path_utils import replace_split_token
from sync_transcode import prepare_working_mov


class PreprocessHandler:
    def __init__(self, mainwindow):
        self.mainwindow = mainwindow
        self.loop_sleep_seconds = 1

    def preprocess_manager(self):
        while not getattr(self.mainwindow, "stop_background_workers", False):
            candidate = self._next_candidate()
            if candidate is None:
                sleep(self.loop_sleep_seconds)
                continue
            self._process_candidate(candidate)

    def _next_candidate(self):
        with self.mainwindow.queue_lock:
            reels = [
                reel
                for reel in self.mainwindow.queue_batches
                if reel.get("state") == "RECORDED" and reel.get("prep_state") == "TO_PREP"
            ]
        if not reels:
            return None
        reels.sort(key=lambda reel: reel.get("time_arrived", ""), reverse=True)
        return reels[0]

    def _target_fps_for_reel(self, reel):
        film_type = reel.get("film_type")
        if film_type in ("R8", "R9", 9.5):
            return 16
        if film_type in ("R16", 16):
            return 24
        return 18

    def _process_candidate(self, candidate):
        reel_id = candidate["id"]
        with self.mainwindow.queue_lock:
            reels = [r for r in self.mainwindow.queue_batches if r.get("id") == reel_id]
            if not reels:
                return
            reel = reels[0]
            if reel.get("prep_state") != "TO_PREP":
                return
            reel["prep_state"] = "PREPARING"
        self.mainwindow.queue_window.update_queue_table.emit()

        try:
            source_path = reel.get("source_video_dir") or reel.get("video_dir")
            if not source_path:
                raise RuntimeError("No source video path set")
            if not str(source_path).lower().endswith(".avi"):
                with self.mainwindow.queue_lock:
                    reel["video_dir"] = source_path
                    reel["working_video_dir"] = source_path
                    reel["video_name"] = os.path.basename(source_path)
                    reel["file_type"] = os.path.splitext(source_path)[1]
                    reel["prep_state"] = "READY"
                self.mainwindow.queue_window.update_queue_table.emit()
                return

            target_fps = self._target_fps_for_reel(reel)
            splits = int(reel.get("splits") or 0)
            prepared_path = None
            if splits > 0:
                for split in range(splits + 1):
                    split_no = split + 1
                    source_split = replace_split_token(source_path, split_no)
                    if not os.path.exists(source_split):
                        raise RuntimeError(f"Missing split source file: {source_split}")
                    output_split = os.path.splitext(source_split)[0] + ".mov"
                    split_prepared = prepare_working_mov(
                        avi_path=source_split,
                        has_sound=bool(reel.get("has_sound")),
                        ffmpeg_path=self.mainwindow.ffmpeg_path,
                        ffprobe_path=self.mainwindow.ffprobe_path,
                        target_fps=target_fps,
                        output_mov_path=output_split,
                    )
                    if split == 0:
                        prepared_path = split_prepared
            else:
                output_mov = reel.get("working_video_dir") or os.path.splitext(source_path)[0] + ".mov"
                prepared_path = prepare_working_mov(
                    avi_path=source_path,
                    has_sound=bool(reel.get("has_sound")),
                    ffmpeg_path=self.mainwindow.ffmpeg_path,
                    ffprobe_path=self.mainwindow.ffprobe_path,
                    target_fps=target_fps,
                    output_mov_path=output_mov,
                )
            with self.mainwindow.queue_lock:
                reel["working_video_dir"] = prepared_path
                reel["video_dir"] = prepared_path
                reel["video_name"] = os.path.basename(prepared_path)
                reel["file_type"] = ".mov"
                reel["prep_state"] = "READY"
                reel.pop("prep_error", None)
            print(f"preprocess complete for reel {reel_id}: {prepared_path}")
        except Exception as exc:
            with self.mainwindow.queue_lock:
                reel["prep_state"] = "FAILED"
                reel["prep_error"] = str(exc)
            print(f"preprocess failed for reel {reel_id}: {exc}")
        finally:
            self.mainwindow.queue_window.update_queue_table.emit()
