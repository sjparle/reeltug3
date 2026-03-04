import argparse
import os
import tempfile
import threading
from types import SimpleNamespace

import cv2
import numpy as np
from PyQt5 import QtWidgets

from config import API_PASSWORD, API_USERNAME
from preview_handler import PreviewHandler
from preprocess_handler import source_split_number_for_output
from split_matcher import match_split_crossover
from ui.workers import workerSignals


def _make_dummy_video(video_path: str, fps: int = 18, frames: int = 180):
    writer = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (640, 360))
    for i in range(frames):
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        frame[:, :, 1] = (i * 2) % 255
        writer.write(frame)
    writer.release()


def queue_fetch_smoke(live_api: bool):
    if live_api:
        if not API_USERNAME or not API_PASSWORD:
            return False, "Live API requested but REELTUG_API_USERNAME/API_PASSWORD are not set."
        from api import MakeRequest

        req = MakeRequest()
        data, response = req.make_get("/cine/cine-tug/get-to-edit-all")
        if not data or not isinstance(data, dict):
            return False, f"Live queue fetch failed with status: {getattr(response, 'status_code', 'unknown')}"
        return True, f"Live queue fetch OK. orders={len(data.get('orders', []))}"
    return True, "Queue fetch dry-run OK (live API not requested)."


def preview_generation_smoke():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        video_path = os.path.join(tmp, "smoke.mp4")
        _make_dummy_video(video_path)

        fake_mainwindow = SimpleNamespace(
            queue_lock=threading.RLock(),
            queue_batches=[],
            previews_loaded=0,
            start_interval=8,
            end_interval=8,
            current_split=0,
            previews_loading=False,
            signal_previews_loaded=SimpleNamespace(emit=lambda *_: None),
        )
        fake_mainwindow.queue_batches.append(
            {"id": 1, "splits": 0, "video_dir": video_path, "preview_loaded": False, "state": "RECORDED"}
        )
        handler = PreviewHandler(fake_mainwindow, workerSignals())
        handler.fetch_previews(1, False)
        reel = fake_mainwindow.queue_batches[0]
        if "preview_data" not in reel or 0 not in reel["preview_data"]:
            return False, "Preview generation failed: no preview_data produced."
        starts = reel["preview_data"][0]["start_previews"]
        ends = reel["preview_data"][0]["end_previews"]
        if len(starts) == 0 or len(ends) == 0:
            return False, "Preview generation failed: empty preview frames."
        return True, f"Preview generation OK. start={len(starts)} end={len(ends)}"


def render_prep_smoke():
    try:
        from render import ProcessVideo
    except ModuleNotFoundError as exc:
        return True, f"Render prep skipped: missing dependency ({exc})."

    fake_render_window = SimpleNamespace(time_start=0)
    fake_mainwindow = SimpleNamespace(req=None, ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", rendering=False)
    processor = ProcessVideo(fake_render_window, fake_mainwindow)
    processor.film_type = "R8"
    processor.splits = 0
    processor.current_split = 0
    processor.reel = {0: {}}
    fps = processor.choose_fps()
    if fps not in (16, 18, 24):
        return False, f"Unexpected fps result from render prep: {fps}"
    return True, f"Render prep OK. choose_fps(R8)={fps}"

def split_remap_smoke():
    # 3 total split files: output SP1/SP2/SP3 should consume source SP3/SP2/SP1 when pre-reversing.
    mapped = [source_split_number_for_output(3, out_split_no, True) for out_split_no in (1, 2, 3)]
    if mapped != [3, 2, 1]:
        return False, f"Unexpected pre-reverse split mapping for 3-way split: {mapped}"
    baseline = [source_split_number_for_output(3, out_split_no, False) for out_split_no in (1, 2, 3)]
    if baseline != [1, 2, 3]:
        return False, f"Unexpected baseline split mapping for 3-way split: {baseline}"
    return True, "Split remap mapping OK."


def split_match_smoke():
    with tempfile.TemporaryDirectory() as tmp:
        split_a = os.path.join(tmp, "split_a.mp4")
        split_b = os.path.join(tmp, "split_b.mp4")
        fps = 18
        frame_count = 220

        writer_a = cv2.VideoWriter(split_a, cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 180))
        writer_b = cv2.VideoWriter(split_b, cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 180))
        if not writer_a.isOpened() or not writer_b.isOpened():
            return False, "Failed to open temporary writers for split match smoke."

        overlap = []
        for i in range(frame_count):
            frame = np.zeros((180, 320, 3), dtype=np.uint8)
            frame[:, :, 0] = (i * 3) % 255
            frame[:, :, 1] = (i * 7) % 255
            cv2.putText(frame, str(i), (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 2)
            if i >= frame_count - 60:
                overlap.append(frame.copy())
            writer_a.write(frame)

        for i in range(60):
            writer_b.write(overlap[i])
        for i in range(160):
            frame = np.zeros((180, 320, 3), dtype=np.uint8)
            frame[:, :, 2] = (i * 11) % 255
            cv2.putText(frame, f"b{i}", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 2)
            writer_b.write(frame)

        writer_a.release()
        writer_b.release()

        match = match_split_crossover(split_a, split_b, sample_fps=4.0, window_seconds=10.0, min_confidence=0.7)
        if not match:
            return False, "Split matcher returned no suggestion on synthetic overlap."
        if "first_split_end_frame" not in match or "second_split_start_frame" not in match or "confidence" not in match:
            return False, f"Split matcher response missing keys: {match}"
        return True, f"Split matcher OK. confidence={match['confidence']}"


def main():
    parser = argparse.ArgumentParser(description="ReelTug smoke tests (non-destructive by default).")
    parser.add_argument("--live-api", action="store_true", help="Run real API queue fetch smoke test.")
    args = parser.parse_args()

    tests = [
        ("queue_fetch", lambda: queue_fetch_smoke(args.live_api)),
        ("preview_generation", preview_generation_smoke),
        ("render_prep", render_prep_smoke),
        ("split_remap", split_remap_smoke),
        ("split_match", split_match_smoke),
    ]

    failures = 0
    for name, fn in tests:
        ok, msg = fn()
        print(f"[{name}] {'PASS' if ok else 'FAIL'}: {msg}")
        if not ok:
            failures += 1

    if failures > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
