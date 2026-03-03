import re
import subprocess
from pathlib import Path
import json
import os


def _parse_log_target_fps(log_path: Path, default_fps: float) -> float:
    if not log_path.exists():
        return default_fps
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if re.fullmatch(r"\d+(\.\d+)?", s):
            return float(s)
    return default_fps


def _parse_dat_rows(dat_path: Path):
    rows = []
    with dat_path.open("r", encoding="utf-8", errors="ignore") as file_obj:
        for raw in file_obj:
            line = raw.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) != 3:
                continue
            try:
                frame_idx = int(parts[0])
                cumulative_ms = int(parts[1])
            except ValueError:
                continue
            # Some files include a very large timestamp token as first row.
            if cumulative_ms > 1_000_000_000:
                continue
            rows.append((frame_idx, cumulative_ms))
    if not rows:
        raise RuntimeError(f"No usable DAT rows found in {dat_path}")
    return rows


def _build_atempo_chain(atempo: float) -> str:
    factors = []
    remaining = atempo
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join([f"atempo={f:.12f}" for f in factors])


def _is_valid_media(path: Path, ffprobe_path: str, min_duration_seconds: float = 1.0) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        info = json.loads(result.stdout or "{}")
        streams = info.get("streams", [])
        has_video = any(stream.get("codec_type") == "video" for stream in streams)
        duration = float(info.get("format", {}).get("duration", "0") or 0)
        return has_video and duration >= min_duration_seconds
    except Exception:
        return False


def _part_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".part")


def find_sync_sidecars(avi_path: str):
    avi = Path(avi_path)
    if avi.suffix.lower() != ".avi":
        return None
    order_dir = avi.parent
    logs_dir = order_dir / "Logs"
    if not logs_dir.exists():
        return None

    avi_name = avi.name
    log_candidates = [
        logs_dir / f"{avi_name}.log",
        logs_dir / f"{avi_name}.txt",
    ]
    dat_candidates = [
        logs_dir / f"{avi_name}.log.dat",
        logs_dir / f"{avi_name}.dat",
    ]

    log_path = next((p for p in log_candidates if p.exists()), None)
    dat_path = next((p for p in dat_candidates if p.exists()), None)

    if dat_path is None:
        return None
    return {
        "avi": avi,
        "log": log_path,
        "dat": dat_path,
    }


def sync_avi_to_mov(
    avi_path: str,
    ffmpeg_path: str,
    ffprobe_path: str,
    target_fps: float,
    output_mov_path: str = None,
    force: bool = False,
    exact_match: bool = False,
):
    sidecars = find_sync_sidecars(avi_path)
    if sidecars is None:
        return None

    avi = Path(avi_path)
    out_mov = Path(output_mov_path) if output_mov_path else avi.with_suffix(".mov")
    if out_mov.exists() and not force and _is_valid_media(out_mov, ffprobe_path):
        return str(out_mov)
    if out_mov.exists() and not _is_valid_media(out_mov, ffprobe_path):
        out_mov.unlink(missing_ok=True)
    out_part = _part_path(out_mov)
    out_part.unlink(missing_ok=True)

    rows = _parse_dat_rows(sidecars["dat"])
    frame_count = rows[-1][0]
    capture_duration = rows[-1][1] / 1000.0

    target_fps = _parse_log_target_fps(sidecars["log"], target_fps) if sidecars["log"] else target_fps
    target_duration = frame_count / target_fps
    setpts_ratio = target_duration / capture_duration
    audio_atempo = 1.0 / setpts_ratio

    vf_chain = f"setpts={setpts_ratio:.12f}*PTS,format=yuv422p10le"
    af_chain = _build_atempo_chain(audio_atempo)

    command = [
        ffmpeg_path,
        "-hide_banner",
        "-y",
        "-i",
        str(avi),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-vf",
        vf_chain,
        "-r",
        str(target_fps),
        "-c:v",
        "prores_ks",
        "-profile:v",
        "2",
        "-vendor",
        "apl0",
        "-af",
        af_chain,
        "-c:a",
        "pcm_s24le",
        "-f",
        "mov",
    ]
    if exact_match:
        command.extend(
            [
                "-t",
                f"{target_duration:.6f}",
                "-frames:v",
                str(frame_count),
            ]
        )
    else:
        command.append("-shortest")
    command.append(str(out_part))
    subprocess.check_call(command)
    if not _is_valid_media(out_part, ffprobe_path):
        out_part.unlink(missing_ok=True)
        raise RuntimeError(f"Sync transcode output validation failed: {out_part}")
    os.replace(str(out_part), str(out_mov))
    return str(out_mov)


def transcode_avi_to_mov(
    avi_path: str,
    ffmpeg_path: str,
    ffprobe_path: str,
    target_fps: float,
    output_mov_path: str = None,
    force: bool = False,
):
    avi = Path(avi_path)
    out_mov = Path(output_mov_path) if output_mov_path else avi.with_suffix(".mov")
    if out_mov.exists() and not force and _is_valid_media(out_mov, ffprobe_path):
        return str(out_mov)
    if out_mov.exists() and not _is_valid_media(out_mov, ffprobe_path):
        out_mov.unlink(missing_ok=True)
    out_part = _part_path(out_mov)
    out_part.unlink(missing_ok=True)

    command = [
        ffmpeg_path,
        "-hide_banner",
        "-y",
        "-i",
        str(avi),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-r",
        str(target_fps),
        "-c:v",
        "prores_ks",
        "-profile:v",
        "2",
        "-vendor",
        "apl0",
        "-pix_fmt",
        "yuv422p10le",
        "-c:a",
        "pcm_s24le",
        "-f",
        "mov",
        str(out_part),
    ]
    subprocess.check_call(command)
    if not _is_valid_media(out_part, ffprobe_path):
        out_part.unlink(missing_ok=True)
        raise RuntimeError(f"Transcode output validation failed: {out_part}")
    os.replace(str(out_part), str(out_mov))
    return str(out_mov)


def prepare_working_mov(
    avi_path: str,
    has_sound: bool,
    ffmpeg_path: str,
    ffprobe_path: str,
    target_fps: float,
    output_mov_path: str = None,
    force: bool = False,
    exact_match: bool = False,
):
    if has_sound:
        sync_result = sync_avi_to_mov(
            avi_path=avi_path,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            target_fps=target_fps,
            output_mov_path=output_mov_path,
            force=force,
            exact_match=exact_match,
        )
        if sync_result:
            return sync_result

    return transcode_avi_to_mov(
        avi_path=avi_path,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        target_fps=target_fps,
        output_mov_path=output_mov_path,
        force=force,
    )
