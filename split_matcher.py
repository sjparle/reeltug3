import math
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


def _read_frame_features(video_path: str, frame_indices: List[int], side: int = 64) -> Tuple[List[int], np.ndarray]:
    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        return [], np.empty((0, side * side), dtype=np.float32)

    valid_indices: List[int] = []
    feature_rows: List[np.ndarray] = []
    for frame_index in frame_indices:
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (side, side), interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        row = gray.astype(np.float32).reshape(-1)
        row -= float(row.mean())
        norm = float(np.linalg.norm(row))
        if norm < 1e-6:
            continue
        row /= norm
        valid_indices.append(int(frame_index))
        feature_rows.append(row)

    capture.release()
    if not feature_rows:
        return [], np.empty((0, side * side), dtype=np.float32)
    return valid_indices, np.vstack(feature_rows)


def _frame_count_and_fps(video_path: str) -> Tuple[int, float]:
    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        return 0, 0.0
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    capture.release()
    return total_frames, fps


def match_split_crossover(
    first_split_path: str,
    second_split_path: str,
    sample_fps: float = 4.0,
    window_seconds: float = 12.0,
    min_confidence: float = 0.83,
) -> Optional[Dict[str, float]]:
    total_a, fps_a = _frame_count_and_fps(first_split_path)
    total_b, fps_b = _frame_count_and_fps(second_split_path)
    if total_a <= 0 or total_b <= 0 or fps_a <= 0 or fps_b <= 0:
        return None

    step_a = max(1, int(round(fps_a / sample_fps)))
    step_b = max(1, int(round(fps_b / sample_fps)))
    tail_window = max(30, int(round(window_seconds * fps_a)))
    head_window = max(30, int(round(window_seconds * fps_b)))

    start_a = max(0, total_a - tail_window)
    end_a = max(0, total_a - 1)
    start_b = 0
    end_b = max(0, min(total_b - 1, head_window))

    indices_a = list(range(start_a, end_a + 1, step_a))
    indices_b = list(range(start_b, end_b + 1, step_b))
    if not indices_a or not indices_b:
        return None

    valid_a, features_a = _read_frame_features(first_split_path, indices_a)
    valid_b, features_b = _read_frame_features(second_split_path, indices_b)
    if features_a.shape[0] == 0 or features_b.shape[0] == 0:
        return None

    # Cosine similarity for all pairs in the sampled windows.
    similarity = np.matmul(features_a, features_b.T)
    best_a_i, best_b_i = np.unravel_index(int(np.argmax(similarity)), similarity.shape)
    best_score = float(similarity[best_a_i, best_b_i])
    if math.isnan(best_score) or best_score < min_confidence:
        return None

    first_end_frame = int(valid_a[best_a_i])
    second_start_frame = int(valid_b[best_b_i])
    if second_start_frame <= 0:
        second_start_frame = 1
    return {
        "first_split_end_frame": first_end_frame,
        "second_split_start_frame": second_start_frame,
        "confidence": round(best_score, 4),
    }
