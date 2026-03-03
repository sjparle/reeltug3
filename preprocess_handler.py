import os
import subprocess
from time import sleep

from path_utils import replace_split_token
from sync_transcode import prepare_working_mov


def source_split_number_for_output(total_splits: int, output_split_no: int, needs_pre_reverse: bool) -> int:
    if total_splits <= 1 or not needs_pre_reverse:
        return output_split_no
    return (total_splits - output_split_no) + 1


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
            working_path = reel.get("working_video_dir") or source_path
            if not source_path:
                raise RuntimeError("No source video path set")
            needs_pre_reverse = bool(reel.get("pre_reverse_required", False))
            source_is_avi = str(source_path).lower().endswith(".avi")
            if not source_is_avi and not needs_pre_reverse:
                with self.mainwindow.queue_lock:
                    reel["video_dir"] = source_path
                    reel["working_video_dir"] = source_path
                    reel["video_name"] = os.path.basename(source_path)
                    reel["file_type"] = os.path.splitext(source_path)[1]
                    reel["prep_state"] = "READY"
                    reel["pre_reversed"] = False
                self.mainwindow.queue_window.update_queue_table.emit()
                return

            target_fps = self._target_fps_for_reel(reel)
            splits = int(reel.get("splits") or 0)
            prepared_path = None
            total_splits = (splits + 1) if splits > 0 else 1
            for split_index in range(total_splits):
                output_split_no = split_index + 1
                source_split_no = source_split_number_for_output(total_splits, output_split_no, needs_pre_reverse)

                source_split = source_path
                output_split = working_path
                if splits > 0:
                    source_split = replace_split_token(source_path, source_split_no)
                    output_split = replace_split_token(working_path, output_split_no)
                if not os.path.exists(source_split):
                    raise RuntimeError(f"Missing split source file: {source_split}")
                output_dir = os.path.dirname(output_split)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)

                intermediate_path = source_split
                if str(source_split).lower().endswith(".avi"):
                    if needs_pre_reverse:
                        intermediate_path = os.path.splitext(output_split)[0] + ".prep.mov"
                    else:
                        intermediate_path = output_split
                    intermediate_path = prepare_working_mov(
                        avi_path=source_split,
                        has_sound=bool(reel.get("has_sound")),
                        ffmpeg_path=self.mainwindow.ffmpeg_path,
                        ffprobe_path=self.mainwindow.ffprobe_path,
                        target_fps=target_fps,
                        output_mov_path=intermediate_path,
                    )

                final_path = intermediate_path
                if needs_pre_reverse:
                    self._reverse_video_for_editing(intermediate_path, output_split)
                    final_path = output_split
                    if intermediate_path != source_split and os.path.exists(intermediate_path):
                        try:
                            os.remove(intermediate_path)
                        except OSError:
                            pass

                if split_index == 0:
                    prepared_path = final_path

            with self.mainwindow.queue_lock:
                reel["working_video_dir"] = prepared_path
                reel["video_dir"] = prepared_path
                reel["video_name"] = os.path.basename(prepared_path)
                reel["file_type"] = ".mov"
                reel["prep_state"] = "READY"
                reel["pre_reversed"] = needs_pre_reverse
                reel.pop("prep_error", None)
            print(f"preprocess complete for reel {reel_id}: {prepared_path}")
        except Exception as exc:
            with self.mainwindow.queue_lock:
                reel["prep_state"] = "FAILED"
                reel["prep_error"] = str(exc)
            print(f"preprocess failed for reel {reel_id}: {exc}")
        finally:
            self.mainwindow.queue_window.update_queue_table.emit()

    def _input_has_audio(self, video_path):
        try:
            result = subprocess.run(
                [
                    self.mainwindow.ffprobe_path,
                    "-v",
                    "error",
                    "-select_streams",
                    "a",
                    "-show_entries",
                    "stream=index",
                    "-of",
                    "csv=p=0",
                    video_path,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return bool(result.stdout.strip())
        except Exception:
            return False

    def _reverse_video_for_editing(self, input_video_path, output_video_path):
        has_audio = self._input_has_audio(input_video_path)
        output_part = output_video_path + ".part"
        if os.path.exists(output_part):
            os.remove(output_part)
        command = [
            self.mainwindow.ffmpeg_path,
            "-hide_banner",
            "-y",
            "-i",
            input_video_path,
        ]
        if has_audio:
            command.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-filter:v",
                    "reverse,hflip,vflip,format=yuv422p10le",
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-shortest",
                    "-c:v",
                    "prores_ks",
                    "-profile:v",
                    "3",
                    "-vendor",
                    "apl0",
                    "-c:a",
                    "pcm_s24le",
                ]
            )
        else:
            command.extend(
                [
                    "-vf",
                    "reverse,hflip,vflip,format=yuv422p10le",
                    "-an",
                    "-c:v",
                    "prores_ks",
                    "-profile:v",
                    "3",
                    "-vendor",
                    "apl0",
                ]
            )
        command.extend(["-f", "mov", output_part])
        subprocess.check_call(command)
        os.replace(output_part, output_video_path)
