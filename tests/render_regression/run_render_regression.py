import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import render as render_module
from render import ProcessVideo


def _run(command: List[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Command failed\n"
            f"cmd: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def _ffprobe_json(ffprobe: str, video_path: Path) -> Dict[str, Any]:
    out = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_name,codec_type,width,height,r_frame_rate,channels,sample_rate,bit_rate:format=duration,size",
            "-of",
            "json",
            str(video_path),
        ]
    )
    return json.loads(out)


def _video_frame_count(ffprobe: str, video_path: Path) -> int:
    out = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames,nb_frames,r_frame_rate,duration",
            "-of",
            "json",
            str(video_path),
        ]
    )
    data = json.loads(out)
    stream = data["streams"][0]
    for key in ("nb_read_frames", "nb_frames"):
        val = stream.get(key)
        if val and str(val).isdigit():
            return int(val)
    fps = float(Fraction(stream["r_frame_rate"]))
    duration = float(stream.get("duration") or 0.0)
    return max(1, int(round(duration * fps)))


def _video_framemd5(ffmpeg: str, video_path: Path) -> str:
    return _run([ffmpeg, "-v", "error", "-i", str(video_path), "-map", "0:v:0", "-f", "framemd5", "-"])


def _audio_md5(ffmpeg: str, video_path: Path) -> Optional[str]:
    probe = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(video_path), "-map", "0:a:0", "-f", "md5", "-"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return None
    line = probe.stdout.strip()
    return line if line.startswith("MD5=") else None


@dataclass
class CaseResult:
    case_id: str
    outputs: List[Dict[str, Any]]


class _DummySignal:
    def emit(self, *_: Any, **__: Any) -> None:
        return


class _DummyRenderWindow:
    def __init__(self) -> None:
        self.time_start = 0
        self.update_render_table_signal = _DummySignal()


class _DummyReq:
    def make_post(self, *_: Any, **__: Any):
        return {"ok": True}, 200

    def make_get(self, *_: Any, **__: Any):
        return {"boxes": []}, 200


class _DummyMainWindow:
    def __init__(self, ffmpeg_path: str, ffprobe_path: str) -> None:
        self.req = _DummyReq()
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.rendering = False


class RegressionProcessVideo(ProcessVideo):
    def create_single_dvd(self):
        self._log("Skipping create_single_dvd in regression harness.")

    def check_complete_make_multi_dvd(self):
        self._log("Skipping check_complete_make_multi_dvd in regression harness.")

    def complete_reel(self):
        self._log("Skipping API complete_reel call in regression harness.")

    def add_audio(self):
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
        timecode_time_start = str(length * 0)
        timecode_length = str(length)
        self.temp_audio_dir = os.path.join(self.processing_video_folder, "temp.mp3")
        self.video_out_name = self.video_name.replace(self.file_type, ".mp4")
        if self.concat_reel is True or self.splits == 0:
            self.video_out_name = render_module.strip_split_token(self.video_out_name)
        else:
            self.video_out_name = render_module.replace_split_suffix(self.video_out_name, f" - Part {(self.current_split + 1)}")
        self.final_video_out_processing_dir = os.path.join(self.processing_video_folder, self.video_out_name)
        self.not_concact_out_list.append(self.final_video_out_processing_dir)
        self._run_command(
            f'{self.ffmpeg_cmd} -hide_banner -y -ss {timecode_time_start} -t {timecode_length} -i "{render_module.cinemusic_path}" '
            f'-c:a libmp3lame "{self.temp_audio_dir}"',
            shell=True,
            check=True,
        )
        self._run_command(
            f'{self.ffmpeg_cmd} -hide_banner -y -i "{self.trim_video_out_dir}" -i "{self.temp_audio_dir}" '
            f'-map 0:v:0 -map 1:a:0 -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -c:a aac -b:a 192k '
            f'-shortest "{self.final_video_out_processing_dir}"',
            shell=True,
            check=True,
        )

    def final_render(self):
        self.video_out_name = self.video_name.replace(self.file_type, ".mp4")
        if self.concat_reel is True or self.splits == 0:
            self.video_out_name = render_module.strip_split_token(self.video_out_name)
        else:
            self.video_out_name = render_module.replace_split_suffix(self.video_out_name, f" - Part {(self.current_split + 1)}")
        self.final_video_out_processing_dir = os.path.join(self.processing_video_folder, self.video_out_name)
        self.not_concact_out_list.append(self.final_video_out_processing_dir)
        self._run_command(
            f'{self.ffmpeg_cmd} -hide_banner -y -i "{self.trim_video_out_dir}" '
            f'-map 0:v:0 -map 0:a? -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -c:a aac -b:a 192k '
            f'"{self.final_video_out_processing_dir}"',
            shell=True,
            check=True,
        )


def _ensure_test_music(ffmpeg: str, out_dir: Path) -> Path:
    music = out_dir / "test_music.mp3"
    _run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=360",
            "-c:a",
            "libmp3lame",
            str(music),
        ]
    )
    return music


def _build_reel(case: Dict[str, Any], source: Path, output_dir: Path, ffprobe: str) -> Dict[str, Any]:
    splits = int(case["splits"])
    reverse_cfg = case["reverse"]
    if len(reverse_cfg) != splits + 1:
        raise ValueError(f"Case {case['id']} reverse config length must be {splits + 1}")

    trim_data: Dict[int, Dict[str, int]] = {}
    split_states: Dict[int, Dict[str, Any]] = {}
    for split in range(splits + 1):
        split_source = source
        if splits > 0:
            split_source = Path(render_module.replace_split_token(str(source), split + 1))
        frame_count = _video_frame_count(ffprobe, split_source)
        trim_data[split] = {"start_frame": 0, "end_frame": max(1, frame_count - 1)}
        split_states[split] = {"reverse": bool(reverse_cfg[split]), "edited": True}

    reel: Dict[str, Any] = {
        "id": 999001 + hash(case["id"]) % 1000,
        "order_number": "TEST-ORDER",
        "item_number": case["id"],
        "video_dir": str(source),
        "video_out_dir": str(output_dir),
        "video_name": source.name,
        "file_type": source.suffix,
        "state": "TO_RENDER",
        "trim_data": trim_data,
        "highlight_data": {},
        "preview_loaded": True,
        "preview_data": {},
        "title": case["id"],
        "subtitle": "",
        "version": "V1",
        "film_type": "S8",
        "increase_fps": False,
        "splits": splits,
        "concat": bool(case["concat"]),
        "add_music": bool(case["add_music"]),
        "single_dvd": False,
        "multi_dvd": False,
        "qc_data": [],
        "has_sound": bool(case["has_sound"]),
    }
    for split_idx, data in split_states.items():
        reel[split_idx] = data
    return reel


def _collect_output_signatures(ffmpeg: str, ffprobe: str, out_dir: Path) -> List[Dict[str, Any]]:
    mp4_files = sorted([path for path in out_dir.glob("*.mp4") if path.is_file()])
    if not mp4_files:
        raise RuntimeError(f"No output files produced in {out_dir}")
    rows: List[Dict[str, Any]] = []
    for path in mp4_files:
        probe = _ffprobe_json(ffprobe, path)
        rows.append(
            {
                "name": path.name,
                "ffprobe": probe,
                "video_framemd5": _video_framemd5(ffmpeg, path),
                "audio_md5": _audio_md5(ffmpeg, path),
            }
        )
    return rows


def _run_case(case: Dict[str, Any], fixtures_root: Path, ffmpeg: str, ffprobe: str, temp_root: Path) -> CaseResult:
    source = fixtures_root / case["input"]
    if not source.exists():
        raise FileNotFoundError(f"Missing fixture file: {source}")
    case_output = temp_root / case["id"] / "out"
    case_output.mkdir(parents=True, exist_ok=True)
    reel = _build_reel(case, source, case_output, ffprobe)

    process = RegressionProcessVideo(_DummyRenderWindow(), _DummyMainWindow(ffmpeg, ffprobe))
    process.process_video(reel)
    return CaseResult(case_id=case["id"], outputs=_collect_output_signatures(ffmpeg, ffprobe, case_output))


def _compare_case(expected: Dict[str, Any], actual: CaseResult) -> List[str]:
    errors: List[str] = []
    expected_outputs = expected.get("outputs", [])
    actual_outputs = actual.outputs
    if len(expected_outputs) != len(actual_outputs):
        errors.append(
            f"expected {len(expected_outputs)} outputs, got {len(actual_outputs)}"
        )
        return errors
    for idx, exp in enumerate(expected_outputs):
        got = actual_outputs[idx]
        if exp["name"] != got["name"]:
            errors.append(f"output[{idx}] name expected {exp['name']} got {got['name']}")
        if exp["video_framemd5"] != got["video_framemd5"]:
            errors.append(f"output[{idx}] video framemd5 changed")
        if exp.get("audio_md5") != got.get("audio_md5"):
            errors.append(f"output[{idx}] audio md5 changed")
        exp_streams = exp["ffprobe"].get("streams", [])
        got_streams = got["ffprobe"].get("streams", [])
        if len(exp_streams) != len(got_streams):
            errors.append(f"output[{idx}] stream count changed")
        exp_duration = float(exp["ffprobe"]["format"].get("duration", 0))
        got_duration = float(got["ffprobe"]["format"].get("duration", 0))
        if abs(exp_duration - got_duration) > 0.1:
            errors.append(
                f"output[{idx}] duration drift {got_duration:.3f}s vs {exp_duration:.3f}s"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Render regression harness for ReelTug.")
    parser.add_argument(
        "--cases-file",
        default=str(Path(__file__).with_name("cases.json")),
        help="Path to regression cases json.",
    )
    parser.add_argument(
        "--baselines",
        default=str(Path(__file__).with_name("baselines.json")),
        help="Path to baseline signatures json.",
    )
    parser.add_argument("--fixtures-root", default="", help="Override fixture directory.")
    parser.add_argument("--case", action="append", default=[], help="Case id to run (repeatable).")
    parser.add_argument("--create-baseline", action="store_true", help="Write new baseline signatures.")
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    ffprobe = shutil.which("ffprobe") or "ffprobe"

    with open(args.cases_file, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    fixtures_root = Path(args.fixtures_root.strip() or os.getenv(cfg["fixtures_root_env"], cfg["default_fixtures_root"]))
    if not fixtures_root.exists():
        raise FileNotFoundError(f"Fixture directory not found: {fixtures_root}")

    selected = cfg["cases"]
    selected_ids = set(args.case)
    if selected_ids:
        selected = [case for case in selected if case["id"] in selected_ids]
        missing = selected_ids - {case["id"] for case in selected}
        if missing:
            raise ValueError(f"Unknown case id(s): {', '.join(sorted(missing))}")

    with tempfile.TemporaryDirectory(prefix="reeltug_regression_") as td:
        temp_root = Path(td)
        test_music = _ensure_test_music(ffmpeg, temp_root)
        render_module.cinemusic_path = str(test_music)

        results: List[CaseResult] = []
        for case in selected:
            print(f"[run] {case['id']}")
            results.append(_run_case(case, fixtures_root, ffmpeg, ffprobe, temp_root))

    serializable = [{"case_id": row.case_id, "outputs": row.outputs} for row in results]
    baselines_path = Path(args.baselines)

    if args.create_baseline:
        baselines_path.parent.mkdir(parents=True, exist_ok=True)
        with open(baselines_path, "w", encoding="utf-8") as f:
            json.dump({"cases": serializable}, f, indent=2)
        print(f"[baseline] wrote {baselines_path}")
        return 0

    if not baselines_path.exists():
        raise FileNotFoundError(f"Baseline file missing: {baselines_path}")

    with open(baselines_path, "r", encoding="utf-8") as f:
        expected = json.load(f)
    expected_map = {row["case_id"]: row for row in expected.get("cases", [])}

    failures = 0
    for result in results:
        if result.case_id not in expected_map:
            print(f"[FAIL] {result.case_id}: missing in baseline")
            failures += 1
            continue
        case_errors = _compare_case(expected_map[result.case_id], result)
        if case_errors:
            failures += 1
            print(f"[FAIL] {result.case_id}")
            for err in case_errors:
                print(f"  - {err}")
        else:
            print(f"[PASS] {result.case_id}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
