import os, subprocess, datetime, shutil, sys, traceback, math
import cv2
from natsort import natsorted
from config import MUSIC_AUDIO_DIR, TEMP_VIDEO_PROCESSING_DIR, RENDER_LOG_DIR, C2D_OUT_DIR, C2D_EXE_DIR, C2D_MENU_DIR
import re
from time import sleep
import requests
import psutil
from path_utils import replace_split_suffix, replace_split_token, strip_split_token
from sync_transcode import sync_avi_to_mov


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

cinemusic_path = resource_path(MUSIC_AUDIO_DIR)

class ProcessVideo:
    REVERSE_CHUNK_THRESHOLD_SECONDS = 300.0

    def __init__(self, render_window, mainwindow):
        self.render_window = render_window
        self.mainwindow = mainwindow
        self.req = self.mainwindow.req
        self.ffmpeg_path = self.mainwindow.ffmpeg_path
        self.ffprobe_path = self.mainwindow.ffprobe_path
        self.ffmpeg_cmd = f'"{self.ffmpeg_path}"'
        self.ffprobe_cmd = f'"{self.ffprobe_path}"'
        self.render_log_dir = RENDER_LOG_DIR
        self.reel_log_path = None
        self.base_video_dir = ""
        self.split_has_audio = {}
        self.any_split_has_audio = False
        self.synced_video_cache = {}

    def _format_cmd(self, command):
        if isinstance(command, (list, tuple)):
            return subprocess.list2cmdline([str(part) for part in command])
        return str(command)

    def _init_reel_log(self, reel):
        os.makedirs(self.render_log_dir, exist_ok=True)
        now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        reel_id = reel.get("id", "unknown")
        item_number = reel.get("item_number", "unknown")
        filename = f"{now}_reel_{reel_id}_item_{item_number}.txt"
        self.reel_log_path = os.path.join(self.render_log_dir, filename)
        self._log("Created reel render log")

    def _log(self, message):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        if self.reel_log_path:
            try:
                with open(self.reel_log_path, "a", encoding="utf-8") as log_file:
                    log_file.write(line)
            except OSError:
                pass
        print(line.strip())

    def _run_command(self, command, shell=True, check=False):
        pretty_command = self._format_cmd(command)
        self._log(f"RUN: {pretty_command}")
        try:
            return_code = subprocess.call(command, shell=shell)
            self._log(f"EXIT({return_code}): {pretty_command}")
            if check and return_code != 0:
                raise subprocess.CalledProcessError(return_code, command)
            return return_code
        except Exception:
            self._log(traceback.format_exc().strip())
            raise

    def _run_check_output(self, command):
        pretty_command = self._format_cmd(command)
        self._log(f"RUN check_output: {pretty_command}")
        try:
            output = subprocess.check_output(command)
            self._log(f"check_output bytes={len(output)}")
            return output
        except Exception:
            self._log(traceback.format_exc().strip())
            raise

    def _get_video_dir_for_split(self, split_index):
        if self.splits > 0:
            return replace_split_token(self.base_video_dir, split_index + 1)
        return self.base_video_dir

    def _target_fps_for_split(self, split_index):
        end_fps = 18
        if self.film_type in ("R8", "R9", 9.5):
            end_fps = 16
        elif self.film_type in ("S8",):
            end_fps = 18
        elif self.film_type in ("R16", 16):
            end_fps = 24
        if self.splits > 0:
            if "fps" in self.reel.get(split_index, {}):
                end_fps = self.reel[split_index]["fps"]
        else:
            if "fps" in self.reel.get(0, {}):
                end_fps = self.reel[0]["fps"]
        return end_fps

    def _maybe_sync_transcode_video(self, input_video_dir, split_index):
        if not str(input_video_dir).lower().endswith(".avi"):
            return input_video_dir
        if not self.has_sound:
            return input_video_dir
        if input_video_dir in self.synced_video_cache:
            return self.synced_video_cache[input_video_dir]

        target_fps = self._target_fps_for_split(split_index)
        self._log(f"Attempting sync transcode for sound AVI split {split_index + 1}: {input_video_dir}")
        output_video_dir = sync_avi_to_mov(
            avi_path=input_video_dir,
            ffmpeg_path=self.ffmpeg_path,
            ffprobe_path=self.ffprobe_path,
            target_fps=target_fps,
        )
        if output_video_dir:
            self._log(f"Sync transcode complete: {output_video_dir}")
            self.synced_video_cache[input_video_dir] = output_video_dir
            return output_video_dir
        self._log("Sync sidecars not found; continuing with AVI input.")
        return input_video_dir

    def _input_has_audio(self, video_path):
        command = [
            self.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-select_streams",
            "a",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        try:
            output = self._run_check_output(command).decode("utf-8", errors="ignore").strip()
        except Exception:
            return False
        return bool(output)

    def _scan_split_audio(self):
        self.split_has_audio = {}
        total_splits = self.splits + 1 if self.splits > 0 else 1
        for split_index in range(total_splits):
            split_video_dir = self._get_video_dir_for_split(split_index)
            has_audio = os.path.exists(split_video_dir) and self._input_has_audio(split_video_dir)
            self.split_has_audio[split_index] = has_audio
            self._log(f"Split {split_index + 1} input audio={has_audio}: {split_video_dir}")
        self.any_split_has_audio = any(self.split_has_audio.values())
        self._log(f"Any split has audio: {self.any_split_has_audio}")

    def _video_duration_seconds(self, video_path):
        command = [
            self.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        output = self._run_check_output(command).decode("utf-8", errors="ignore").strip()
        return float(output)

    def _reverse_in_chunks(self, input_video_dir, output_video_dir, preserve_or_pad_audio):
        chunk_seconds = 45.0
        duration = self._video_duration_seconds(input_video_dir)
        if duration <= 0:
            raise RuntimeError(f"Unable to determine duration for reverse fallback: {input_video_dir}")

        chunk_count = int(math.ceil(duration / chunk_seconds))
        self._log(
            f"Reverse fallback (chunked) for {input_video_dir}: duration={duration:.2f}s, "
            f"chunk_seconds={chunk_seconds}, chunks={chunk_count}"
        )

        reversed_chunks = []
        for chunk_index in range(chunk_count):
            chunk_start = chunk_index * chunk_seconds
            chunk_len = min(chunk_seconds, max(0.0, duration - chunk_start))
            if chunk_len <= 0:
                continue
            chunk_out = os.path.join(self.processing_video_folder, f"{self.reel_id}-revchunk-{chunk_index:04d}.mov")
            reverse_chunk_cmd = (
                f'{self.ffmpeg_cmd} -hide_banner -y -ss {chunk_start:.6f} -t {chunk_len:.6f} '
                f'-i "{input_video_dir}" -vf "reverse,hflip,vflip,format=yuv422p10le" -an '
                f'-c:v prores_ks -profile:v 3 -vendor apl0 "{chunk_out}"'
            )
            self._run_command(reverse_chunk_cmd, shell=True, check=True)
            reversed_chunks.append(chunk_out)

        if not reversed_chunks:
            raise RuntimeError("Reverse fallback produced no chunks.")

        concat_file_path = os.path.join(self.processing_video_folder, f"{self.reel_id}-reverse-concat.txt")
        with open(concat_file_path, "w", encoding="utf-8") as concat_file:
            for chunk_path in reversed(reversed_chunks):
                concat_file.write(f"file '{chunk_path}'\n")

        concat_out = os.path.join(self.processing_video_folder, f"{self.reel_id}-reversed-video.mov")
        concat_cmd = (
            f'{self.ffmpeg_cmd} -hide_banner -y -f concat -safe 0 -i "{concat_file_path}" '
            f'-c copy "{concat_out}"'
        )
        self._run_command(concat_cmd, shell=True, check=True)

        if preserve_or_pad_audio:
            add_silence_cmd = (
                f'{self.ffmpeg_cmd} -hide_banner -y -i "{concat_out}" '
                f'-f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 '
                f'-map 0:v:0 -map 1:a:0 -shortest -c:v copy -c:a pcm_s16le "{output_video_dir}"'
            )
            self._run_command(add_silence_cmd, shell=True, check=True)
        else:
            if os.path.exists(output_video_dir):
                os.remove(output_video_dir)
            os.replace(concat_out, output_video_dir)
    
    def process_video(self, reel):
        self.mainwindow.rendering = True
        self.reel = reel
        self._init_reel_log(self.reel)
        self._log("Starting render pipeline")
        try:
            self._load_reel_context()
            self._prepare_processing_folder()
            self._set_reel_state("RENDERING")
            multi_split_reel = self._run_split_or_single_pipeline()
            self._run_final_audio_or_finalize(multi_split_reel)
            self._publish_outputs()
            self._complete_reel_flow()
            self._cleanup_processing_folder()
            self._log("Render pipeline complete")
        except Exception:
            self._log(traceback.format_exc().strip())
            raise
        finally:
            self.mainwindow.rendering = False

    def _set_reel_state(self, state):
        self.reel["state"] = state
        self.render_window.update_render_table_signal.emit()
        self._log(f"State -> {state}")

    def _is_convertx_running(self):
        try:
            for proc in psutil.process_iter(attrs=["name"]):
                if proc.info.get("name") == "ConvertXtoDvd.exe":
                    return True
        except Exception as exc:
            self._log(f"ConvertX process check failed: {exc}")
        return False

    def _wait_for_convertx(self, resume_state="FINISHING UP"):
        if not self._is_convertx_running():
            return
        self._set_reel_state("WAITING_FOR_CONVERTX")
        self._log("Waiting for ConvertXtoDvd.exe to finish")
        while self._is_convertx_running():
            sleep(1)
        self._log("ConvertX wait complete")
        if resume_state:
            self._set_reel_state(resume_state)

    def _replace_move(self, src_path, dst_path):
        if os.path.exists(dst_path):
            self._log(f"Destination exists, replacing: {dst_path}")
            if os.path.isdir(dst_path):
                shutil.rmtree(dst_path)
            else:
                os.remove(dst_path)
        shutil.move(src_path, dst_path)

    def _load_reel_context(self):
        print("reel: ", self.reel)
        self.concat_list = []
        self.not_concact_out_list = []
        self.order_number = self.reel["order_number"]
        self.reel_id = self.reel["id"]
        self.video_dir = self.reel["video_dir"]
        self.base_video_dir = self.video_dir
        self.out_dir = self.reel["video_out_dir"]
        self.add_music = self.reel["add_music"]
        self.title = self.reel["title"]
        self.subtitle = self.reel["subtitle"]
        self.version = self.reel["version"]
        self.item_number = self.reel["item_number"]
        self.film_type = self.reel["film_type"]
        self.video_name = self.reel["video_name"]
        self.increase_fps = self.reel["increase_fps"]
        self.current_split = 0
        self.splits = self.reel["splits"]
        self.time_start = self.render_window.time_start
        self.concat_reel = self.reel["concat"]
        self.file_type = self.reel["file_type"]
        print("CONCAT REEL: ", self.concat_reel)
        self.single_dvd = self.reel["single_dvd"]
        self.multi_dvd = self.reel["multi_dvd"]
        self.has_sound = self.reel.get("has_sound")
        if self.has_sound is None:
            self.has_sound = any(comment.get("content_int") == 9 for comment in self.reel.get("qc_data", []))
        print("SINGLE DVD", self.single_dvd)
        self._log(
            f"Reel context: id={self.reel_id}, order={self.order_number}, item={self.item_number}, "
            f"splits={self.splits}, concat={self.concat_reel}, add_music={self.add_music}, "
            f"increase_fps={self.increase_fps}, single_dvd={self.single_dvd}, multi_dvd={self.multi_dvd}, "
            f"has_sound={self.has_sound}"
        )
        self._scan_split_audio()
        self.end_fps = self.choose_fps()

    def _prepare_processing_folder(self):
        self.processing_video_folder = os.path.join(TEMP_VIDEO_PROCESSING_DIR, "processing", str(self.reel_id))
        if os.path.exists(self.processing_video_folder):
            self.remove_processing_folder()
        os.makedirs(self.processing_video_folder, exist_ok=True)
        self._log(f"Processing folder ready: {self.processing_video_folder}")

    def _prepare_split_video(self, split):
        self.video_dir = self._get_video_dir_for_split(split)
        self.video_dir = self._maybe_sync_transcode_video(self.video_dir, split)
        self.video = cv2.VideoCapture(self.video_dir)
        self.fps = self.video.get(cv2.CAP_PROP_FPS)
        print("fps", self.fps, os.path.exists(self.video_dir), self.video_dir)
        self.total_frames = int(self.video.get(cv2.CAP_PROP_FRAME_COUNT))
        self.video.release()
        self.current_split = split
        self.reel_id = str(self.reel["id"]) + "_" + str(split)

    def _process_single_split(self, split):
        self._prepare_split_video(split)
        self._set_reel_state("TRIMMING")
        self.trim()
        split_cfg = self.reel.get(split, {})
        split_reverse = bool(split_cfg.get("reverse", False))
        self._log(f"Split {split + 1} reverse={split_reverse}")
        if split_reverse:
            self._set_reel_state("REVERSING")
            self.reverse()
        if self.increase_fps is True:
            self.interpolate()
        # Ensure split concat uses the final processed clip (trim/reverse/interpolate), not the pre-reverse trim output.
        if self.splits > 0 and self.concat_reel is True and len(self.concat_list) > split:
            self.concat_list[split] = self.trim_video_out_dir
        self._log(f"Split {split + 1} final clip path: {self.trim_video_out_dir}")

    def _finalize_non_concat_split(self):
        self.single_dvd = False
        self.end_fps = self.choose_fps()
        if self.add_music is True:
            self._set_reel_state("ADDING AUDIO")
            print("adding audio")
            self.add_audio()
        else:
            self._set_reel_state("FINISHING UP")
            print("final render")
            self.final_render()

    def _run_split_pipeline(self):
        multi_split_reel = False
        for split in range(self.splits + 1):
            self._process_single_split(split)
            if self.concat_reel is False:
                multi_split_reel = True
                self._finalize_non_concat_split()
        if self.concat_reel is True:
            all_splits_reversed = all(
                bool(self.reel.get(split, {}).get("reverse", False)) for split in range(self.splits + 1)
            )
            if all_splits_reversed and self.splits > 0:
                self._log("All splits are reversed; using reverse split concat order.")
                self.concat_list = list(reversed(self.concat_list))
            self.concat()
        return multi_split_reel

    def _run_single_reel_pipeline(self):
        self.video_dir = self.base_video_dir
        self.video_dir = self._maybe_sync_transcode_video(self.video_dir, 0)
        self.video = cv2.VideoCapture(self.video_dir)
        self.fps = self.video.get(cv2.CAP_PROP_FPS)
        print("fps", self.fps, os.path.exists(self.video_dir), self.video_dir)
        self.total_frames = int(self.video.get(cv2.CAP_PROP_FRAME_COUNT))
        self.video.release()
        self.current_split = 0

        self._set_reel_state("TRIMMING")
        self.trim()
        if self.reel[0]["reverse"] is True:
            self._set_reel_state("REVERSING")
            self.reverse()
        if self.increase_fps is True:
            self.interpolate()
        return False

    def _run_split_or_single_pipeline(self):
        if self.splits > 0:
            return self._run_split_pipeline()
        return self._run_single_reel_pipeline()

    def _run_final_audio_or_finalize(self, multi_split_reel):
        print("SHOULD BE RUNNING ONE OF THESE????, ", self.add_music, multi_split_reel, self.single_dvd)
        if multi_split_reel is True:
            return
        if self.add_music is True:
            self._set_reel_state("ADDING AUDIO")
            print("adding audio")
            self.add_audio()
        else:
            self._set_reel_state("FINISHING UP")
            print("final render")
            self.final_render()

    def _publish_outputs(self):
        if os.path.exists("R:"):
            print("R: drive is available.")
        else:
            print("R: drive is NOT available.")
        os.makedirs(self.out_dir, exist_ok=True)
        print("MULTI DVD", self.multi_dvd)
        print(self.not_concact_out_list)

        if self.splits > 0 and self.concat_reel is False:
            for video in self.not_concact_out_list:
                shutil.move(video, self.out_dir)
            return

        video_file_out_dir = os.path.join(self.out_dir, self.video_out_name)
        shutil.move(self.final_video_out_processing_dir, video_file_out_dir)

    def _complete_reel_flow(self):
        if self.single_dvd is True:
            self.create_single_dvd()
        self.complete_reel()
        if self.multi_dvd is True:
            dvd_out_dir = self.out_dir.replace("CINE", "DVD")
            os.makedirs(dvd_out_dir, exist_ok=True)
            self.check_complete_make_multi_dvd()
        self._set_reel_state("DONE")
        self.render_window.update_render_table_signal.emit()

    def _cleanup_processing_folder(self):
        sleep(1)
        self.remove_processing_folder()
        self._log("Processing folder removed")
    
    def remove_processing_folder(self):
        """remove all files in processing folder"""
        for file in os.listdir(self.processing_video_folder):
            file_path = os.path.join(self.processing_video_folder, file)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path): shutil.rmtree(file_path)
            except Exception as e:
                print(e)
        os.rmdir(self.processing_video_folder)



    def reverse(self):
        video_out_name = f"{self.reel_id}-reversed.mov"
        self.processing_reverse_video_dir = os.path.join(self.processing_video_folder, video_out_name)
        input_video_dir = self.trim_video_out_dir
        print(
            "currently reversing",
            self.reel_id,
            "split",
            self.current_split,
            "video_dir",
            input_video_dir,
            "out",
            self.processing_reverse_video_dir,
        )
        preserve_or_pad_audio = self.any_split_has_audio and not self.add_music
        if preserve_or_pad_audio:
            reverse_cmd = (
                f'{self.ffmpeg_cmd} -hide_banner -y -i "{input_video_dir}" '
                f'-f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 '
                f'-filter:v "reverse,hflip,vflip,format=yuv422p10le" -map 0:v:0 -map 1:a:0 -shortest '
                f'-c:v prores_ks -profile:v 3 -vendor apl0 -c:a pcm_s16le "{self.processing_reverse_video_dir}"'
            )
        else:
            reverse_cmd = (
                f'{self.ffmpeg_cmd} -hide_banner -y -i "{input_video_dir}" '
                f'-vf "reverse,hflip,vflip,format=yuv422p10le" -an -c:v prores_ks -profile:v 3 -vendor apl0 '
                f'"{self.processing_reverse_video_dir}"'
            )
        input_duration = None
        try:
            input_duration = self._video_duration_seconds(input_video_dir)
        except Exception as exc:
            self._log(f"Could not probe reverse input duration, using primary reverse first: {exc}")
        if input_duration is not None and input_duration > self.REVERSE_CHUNK_THRESHOLD_SECONDS:
            self._log(
                f"Input duration {input_duration:.2f}s exceeds threshold "
                f"{self.REVERSE_CHUNK_THRESHOLD_SECONDS:.0f}s; using chunked reverse by default."
            )
            self._reverse_in_chunks(input_video_dir, self.processing_reverse_video_dir, preserve_or_pad_audio)
            self.trim_video_out_dir = self.processing_reverse_video_dir
            return
        try:
            self._run_command(reverse_cmd, shell=True, check=True)
        except subprocess.CalledProcessError:
            self._log("Primary reverse failed; retrying with chunked reverse fallback to reduce memory usage.")
            self._reverse_in_chunks(input_video_dir, self.processing_reverse_video_dir, preserve_or_pad_audio)
        self.trim_video_out_dir = self.processing_reverse_video_dir
        
    def trim(self):
        self.start_frame = self.reel['trim_data'][self.current_split]['start_frame']
        self.end_frame = self.reel['trim_data'][self.current_split]['end_frame']
        video_dir = self.video_dir
        print(self.start_frame, self.end_frame, self.fps)
        trim_start_time = self.start_frame / self.fps
        trim_start_timecode = str(datetime.timedelta(seconds=trim_start_time))
        trim_end_time = self.end_frame / self.fps
        trim_end_timecode = str(datetime.timedelta(seconds=trim_end_time))
        print("TIMECODE DARTA:", trim_start_time, trim_start_timecode, trim_end_time, trim_end_timecode)

        video_out_name = str(self.reel_id) + ".mov"
        print("video_out_name", video_out_name, "processing fol", self.processing_video_folder)
        self.trim_video_out_dir = os.path.join(self.processing_video_folder, video_out_name)
        self.concat_list.append(self.trim_video_out_dir)
        preserve_or_pad_audio = self.any_split_has_audio and not self.add_music
        if preserve_or_pad_audio and self.reel[self.current_split]['reverse'] is True:
            self._log("Reverse is enabled for this split; source audio is not preserved, padding with silent audio.")
            self._run_command(
                f'{self.ffmpeg_cmd} -hide_banner -y -ss {trim_start_timecode} -to {trim_end_timecode} -i "{video_dir}" -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 -map 0:v:0 -map 1:a:0 -shortest -vf "format=yuv422p10le" -c:v prores_ks -profile:v 3 -vendor apl0 -c:a pcm_s16le "{self.trim_video_out_dir}"',
                shell=True,
            )
        elif preserve_or_pad_audio and self.reel[self.current_split]['reverse'] is False:
            split_has_audio = self.split_has_audio.get(self.current_split, False)
            if split_has_audio:
                self._run_command(
                    f'{self.ffmpeg_cmd} -hide_banner -y -ss {trim_start_timecode} -to {trim_end_timecode} -i "{video_dir}" -map 0:v:0 -map 0:a:0 -vf "format=yuv422p10le" -c:v prores_ks -profile:v 3 -vendor apl0 -c:a pcm_s16le "{self.trim_video_out_dir}"',
                    shell=True,
                )
            else:
                self._run_command(
                    f'{self.ffmpeg_cmd} -hide_banner -y -ss {trim_start_timecode} -to {trim_end_timecode} -i "{video_dir}" -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 -map 0:v:0 -map 1:a:0 -shortest -vf "format=yuv422p10le" -c:v prores_ks -profile:v 3 -vendor apl0 -c:a pcm_s16le "{self.trim_video_out_dir}"',
                    shell=True,
                )
        else:
            self._run_command(
                f'{self.ffmpeg_cmd} -hide_banner -y -ss {trim_start_timecode} -to {trim_end_timecode} -i "{video_dir}" -vf "format=yuv422p10le" -c:v prores_ks -profile:v 3 -vendor apl0 "{self.trim_video_out_dir}"',
                shell=True,
            )

    def interpolate(self):
        video_out_name = str(self.reel_id) + "-interpolated.mov"
        interpolate_out_dir = os.path.join(self.processing_video_folder, video_out_name)
        command = (
            f'cd "C:\\reelTug\\interpolate\\RIFE";'
            f".\\.env\\Scripts\\activate;"
            f'py inference_video.py --exp=1 --video="{self.trim_video_out_dir}" --ext=".mov" --output="{interpolate_out_dir}"'
        )
        self._run_command(command, shell=True)
        self.trim_video_out_dir = interpolate_out_dir

        self._run_command(command, shell=True)
       
    def choose_fps(self):
        if self.film_type == "R8" or self.film_type == "R9" or self.film_type == 9.5:
            end_fps = 16
        elif self.film_type == "S8":
            end_fps = 18
        elif self.film_type == "R16" or self.film_type == 16:
            end_fps = 24
        if self.splits > 0:
            if "fps" in self.reel[self.current_split]:
                end_fps = self.reel[self.current_split]['fps']
        else:
            if "fps" in self.reel[0]:
                end_fps = self.reel[0]['fps']
        return end_fps


    def concat(self):
        """create text file"""
        concat_file_path = os.path.join(self.processing_video_folder, "concat.txt")
        concat_file = open(concat_file_path, "w")
        for index, path in enumerate(self.concat_list):
            self._log(f"Concat input {index + 1}: {path}")
            concat_file.write(f"file '{path}'\n")
        concat_file.close()
        self._log(f"Concat manifest written: {concat_file_path}")

        """concatenate video"""
        self.reel_id = self.reel['id']
        video_out_name = str(self.reel_id) + ".mov"
        self.trim_video_out_dir = os.path.join(self.processing_video_folder, video_out_name)
        print("concat to here: ", self.trim_video_out_dir)
        self._run_command(
            f'{self.ffmpeg_cmd} -hide_banner -y -f concat -safe 0 -i "{concat_file_path}" -c copy "{self.trim_video_out_dir}"',
            shell=True,
        )

    
    def check_audio(self):
        result = self._run_check_output(
            [self.ffprobe_path, "-i", self.trim_video_out_dir, "-show_streams", "-select_streams", "a", "-loglevel", "error"]
        )
        if result:
            has_audio = True
        else:
            has_audio = False
        return has_audio


    def add_audio(self):
        # print("adding audio")
        # if self.splits == 0:
        result = self._run_check_output(
            [
                self.ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                self.trim_video_out_dir,
            ]
        )
        length = float(result)
        time_diff = 10440 - (self.time_start + length)
        if time_diff < 0:
            self.time_start = 0
        # else:
        #     length = 0
        #     for split in range(self.splits + 1):
        #         result = subprocess.check_output(f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{self.concat_list[split]}"')
        #         length = length + float(result)

        timecode_time_start = str(datetime.timedelta(seconds=self.time_start))
        timecode_length = str(datetime.timedelta(seconds=length))
        self.temp_audio_dir = os.path.join(self.processing_video_folder, "temp.mp3")
        self.video_out_name = self.video_name.replace(self.file_type, ".mp4")
        
        if self.concat_reel == True or self.splits == 0:
            self.video_out_name = strip_split_token(self.video_out_name)
        else:
            self.video_out_name = replace_split_suffix(self.video_out_name, f" - Part {(self.current_split + 1)}")
        self.final_video_out_processing_dir = os.path.join(self.processing_video_folder, self.video_out_name)
        print("file input dir is: ", self.trim_video_out_dir)
        self.not_concact_out_list.append(self.final_video_out_processing_dir)
        self._run_command(
            f'{self.ffmpeg_cmd} -hide_banner -ss {timecode_time_start} -t {timecode_length} -y -i "{cinemusic_path}" -c copy "{self.temp_audio_dir}"',
            shell=True,
        )
        self._run_command(
            f'{self.ffmpeg_cmd} -hide_banner -y -r {self.end_fps} -hwaccel cuda -hwaccel_output_format cuda -i "{self.trim_video_out_dir}" -i "{self.temp_audio_dir}" -fps_mode auto -map 0:v -map 1:a -b:v 20M -c:v h264_nvenc -preset slow -pix_fmt yuv420p -shortest "{self.final_video_out_processing_dir}"',
            shell=True,
        )
        self.time_start = self.time_start + length


    def final_render(self):
        print("BEFORE RENAME: ", self.film_type, self.video_name)
        self.video_out_name = self.video_name.replace(self.file_type, ".mp4")
        print("after rename: ", self.video_out_name)
        if self.concat_reel == True or self.splits == 0:
            self.video_out_name = strip_split_token(self.video_out_name)
        else:
            self.video_out_name = replace_split_suffix(self.video_out_name, f" - Part {(self.current_split + 1)}")
        print("RENAMING, new: ", self.video_out_name)
        self.final_video_out_processing_dir = os.path.join(self.processing_video_folder, self.video_out_name)
        print("file input dir is: ", self.trim_video_out_dir, "output_dir is: ", self.final_video_out_processing_dir)
        self.not_concact_out_list.append(self.final_video_out_processing_dir)
        self._run_command(
            f'{self.ffmpeg_cmd} -hide_banner -y -r {self.end_fps} -hwaccel cuda -hwaccel_output_format cuda -i "{self.trim_video_out_dir}" -fps_mode auto -map 0:v:0 -map 0:a? -b:v 20M -c:v h264_nvenc -preset slow -pix_fmt yuv420p -c:a aac -b:a 192k "{self.final_video_out_processing_dir}"',
            shell=True,
        )

    def create_single_dvd(self):
        # Prepare API call to DVD Author service
        api_url = "http://10.0.0.54:5000/run-dvd-author"
        input_folder = f"/7 - Transferring/{self.order_number}/CINE"
        output_folder = f"/7 - Transferring/{self.order_number}/DVD"
        print("input_folder: ", input_folder, "output_folder: ", output_folder)

        payload = {
            "input_folder": input_folder,
            "output_folder": output_folder,
            "file_names": []
        }

        try:
            response = requests.post(api_url, json=payload)
            response.raise_for_status()
            print(f"DVD render job started. Monitoring {output_folder}/temp for completion...")

        except requests.exceptions.RequestException as e:
            print(f"Error making API request: {str(e)}")
        except Exception as e:
            print(f"Unexpected error: {str(e)}")

    def complete_reel(self):
        req_dict = {}
        req_dict['reels'] = [self.reel_id]
        req_dict['changes'] = [{"key" : "state", "value" : 6}]
        data, res = self.req.make_post("/cine/cine-tug/edit", req_dict)
        # if self.single_dvd == True:
        #     dvd_dict = {}
        #     dvd_dict['order_key'] = self.video_out_name.split(" - ")[0]
        #     dvd_dict['file_name'] = self.mpg_out_name
        #     dvd_dict['orderNumber'] = self.order_number
        #     dvd_req = requests.post("http://10.0.0.123:5643/api/author-dvd-cine", json=dvd_dict)
        #     print(dvd_req.status_code, dvd_req.text)
        print(data, res)

    def check_complete_make_multi_dvd(self):
        print(f"Starting check_complete_make_multi_dvd for order {self.order_number}")
        data, res = self.req.make_get(f"/orders/{self.order_number}")
        print(f"API response for /orders/{self.order_number}: status={res}, data keys={list(data.keys()) if isinstance(data, dict) else type(data)}")

        cine_boxes = [d for d in data['boxes'] if d['format_items'] == "CINE"]
        print(f"Found {len(cine_boxes)} CINE boxes")
        if not cine_boxes:
            print("No CINE boxes found, aborting multi DVD check.")
            return
        cine_box = cine_boxes[0]
        print(f"CINE box keys: {list(cine_box.keys())}")

        unedited_reels = [d for d in cine_box['reels'] if d['state'] == "TO RECORD" or d['state'] == "RECORDING" or d['state'] == "RECORDED"]
        print(f"Unedited reels: {len(unedited_reels)}")
        if len(unedited_reels) > 0:
            print("There are still unedited reels, exiting function.")
            return

        passed_reels = [d for d in cine_box['reels'] if d['state'] == "PASS" or  d['state'] == "EDITED"]
        print(f"Passed/Edited reels: {len(passed_reels)}")
        passed_reel_list = []
        for reel in passed_reels:
            print(f"Adding reel item_number: {reel['item_number']}")
            passed_reel_list.append(reel['item_number'])
        sorted_list = natsorted(passed_reel_list)
        print(f"Sorted list of passed reel item_numbers: {sorted_list}")

        out_dir = rf"R:\7 - Transferring\{self.order_number}\CINE"
        print(f"Output directory: {out_dir}")
        playlist_dir = os.path.join(out_dir, "playlist.txt")
        print(f"Playlist file will be: {playlist_dir}")
        dvd_out_dir = out_dir.replace("CINE", "DVD")
        print(f"DVD output directory: {dvd_out_dir}")
        os.makedirs(dvd_out_dir, exist_ok=True)
        final_dvd_out_name = f"{self.order_number} - Cine Film Memories"
        final_dvd_out_dir = os.path.join(dvd_out_dir, final_dvd_out_name)
        base_final_dvd_out_dir = final_dvd_out_dir
        print(f"Final DVD output directory: {final_dvd_out_dir}")
        c2d_out_dir = C2D_OUT_DIR
        print(f"C2D output directory: {c2d_out_dir}")

        total_parts = 0

        playlist = open(playlist_dir, "w")
        for r, d, f in os.walk(out_dir):                        
            for file in f:
                if "Reel" in file:
                    if file.startswith("Reel"):                 #new
                        continue
                    split_file = file.split(" Reel ")[-1]
                    remove_version = re.sub(r"V\d", "", split_file)
                    add_reel_prefix = f"Reel {remove_version}"
                    dest = os.path.join(r, add_reel_prefix)
                    if os.path.exists(dest):
                        os.remove(dest)
                    os.rename(os.path.join(r, file), dest)
        playlist.close()
        
        total_splits = 0
        total_size = 0

        playlist = open(playlist_dir, "w")
        print(sorted_list)
        for reel in sorted_list:
            prefix = f"Reel {reel}"
            print(prefix)
            found_files = []
            for r, d, f in os.walk(out_dir):
                for file in f:
                    if file.startswith(prefix):
                        try:
                            temp_str = file.replace(prefix, "")
                            int(temp_str[0])      
                        except:
                            write_str = os.path.join(out_dir, f"{file}\n")
                            playlist.write(write_str)
                            print("writing ", write_str)
        playlist.close()

        total_size = 0
        total_splits = 0

        for r, d, f in os.walk(out_dir):
            for file in f:
                if "Reel" in file:
                    file_dir = os.path.join(r, file)
                    print(str(os.path.getsize(file_dir)))
                    total_size += os.path.getsize(file_dir)
        if (total_size / 1000000000) > 120:
            total_splits = 4
        elif (total_size / 1000000000) > 90:
            total_splits = 3
        elif (total_size / 1000000000) > 60:
            total_splits = 2
        elif (total_size / 1000000000) > 30:
            total_splits = 1

        smallfile = None
        volume_number = 0

        playlist = open(playlist_dir, "r")
        if total_splits > 0:
            num_lines = sum(1 for line in playlist)
            lines_per_file = round(num_lines / total_splits)
            with open(playlist_dir, "r") as bigfile:
                for lineno, line in enumerate(bigfile):
                    print(lineno, lines_per_file)
                    if lineno % lines_per_file == 0:
                        if smallfile:
                            smallfile.close()
                        volume_number += 1
                        small_filename = 'playlist_{}.txt'.format(volume_number)
                        small_filename_dir = os.path.join(out_dir, small_filename)
                        smallfile = open(small_filename_dir, "w")
                    smallfile.write(line)
                if smallfile:
                    smallfile.close()
        playlist.close()

        print("playlist closed, doing next bit", total_splits)

        if total_splits == 0:
            with open(playlist_dir) as f:
                folder_out_name = f.readline()
            folder_out_name = folder_out_name.replace("\n", "").split("\\")[-1].replace(".mp4", "")
            f.close()

            c2d_folder_out_dir = os.path.join(c2d_out_dir, folder_out_name)
                                                                        
            if len(unedited_reels) == 0:
                exe_dir = C2D_EXE_DIR
                menu_dir = C2D_MENU_DIR
                command_str = f'"{exe_dir}" /fl="{playlist_dir}" /overwrite=true /auto=true /menu="{menu_dir}" /close=true'
                print(command_str)
                
                self._run_command(command_str, shell=True, check=True)
                self._wait_for_convertx()

                print("moving from: ", c2d_folder_out_dir, "to: ", final_dvd_out_dir)
                self._replace_move(c2d_folder_out_dir, final_dvd_out_dir)
                os.remove(playlist_dir)


        else:
            if len(unedited_reels) == 0:
                for volume in range(volume_number):
                    volume_no = volume + 1
                    playlist_str = f"playlist_{volume_no}.txt"
                    playlist_dir = os.path.join(out_dir, playlist_str)
                    print("doing playlist: ", playlist_str)
                    with open(os.path.join(out_dir, playlist_str)) as f:
                        folder_out_name = f.readline()
                    folder_out_name = folder_out_name.replace("\n", "").split("\\")[-1].replace(".mp4", "")
                    f.close()

                    c2d_folder_out_dir = os.path.join(c2d_out_dir, folder_out_name)
                    exe_dir = C2D_EXE_DIR
                    menu_dir = C2D_MENU_DIR
                    command_str = f'"{exe_dir}" /fl="{playlist_dir}" /overwrite=true /auto=true /menu="{menu_dir}" /close=true'
                    self._wait_for_convertx()
                    self._run_command(command_str, shell=True, check=True)
                    self._wait_for_convertx()
                    volume_dvd_out_dir = f"{base_final_dvd_out_dir} - Volume {volume_no}"
                    print("moving from: ", c2d_folder_out_dir, "to: ", volume_dvd_out_dir)
                    self._replace_move(c2d_folder_out_dir, volume_dvd_out_dir)
                    os.remove(playlist_dir)
