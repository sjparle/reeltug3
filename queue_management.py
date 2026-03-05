import os
from typing import Any, Dict, Optional, Tuple

import requests

from config import CINE_EDITING_DIR, TEMP_VIDEO_PROCESSING_DIR, TRANSFERRING_DIRECTORY, QUEUE_FETCH_TIMEOUT_SECONDS
from path_utils import replace_split_token
from reel_models import ReelBatch


class QueueManagement:
    def __init__(self, mainwindow, queue):
        self.mainwindow = mainwindow
        self.queue_window = queue

    def queue_handler(self):
        try:
            queue_request, response = self.mainwindow.req.make_get(
                "/cine/cine-tug/get-to-edit-all-ai",
                timeout=QUEUE_FETCH_TIMEOUT_SECONDS,
                retries=1,
            )
        except requests.exceptions.ConnectTimeout:
            print("could not connect to OrderFlow")
            self.queue_window.queue_connected = False
            return

        if not queue_request or not isinstance(queue_request, dict):
            status = response.status_code if response is not None else "unknown"
            if response is None and (not self.mainwindow.req.username or not self.mainwindow.req.password):
                print("failed to get queue data. missing API credentials (REELTUG_API_USERNAME/REELTUG_API_PASSWORD)")
            else:
                print(f"failed to get queue data. status={status}")
            if getattr(self.mainwindow.req, "last_error", ""):
                print(f"queue error detail: {self.mainwindow.req.last_error}")
            self.queue_window.queue_connected = False
            return

        self.queue_window.queue_connected = True
        discovered_reels = []
        for order in queue_request.get("orders", []):
            try:
                self._process_order(order, discovered_reels)
            except Exception as exc:
                print(exc)
                continue
        self._merge_queue_reels(discovered_reels)

        self.queue_window.update_queue_table.emit()
        if not self.mainwindow.caching_previews:
            self.mainwindow.signal_start_preview_manager.emit()

    def _process_order(self, order: Dict[str, Any], discovered_reels):
        number_of_reels = order["total_reels"]
        dvd_output_number = self.get_output_number(order["order"], "DVD Set")
        single_dvd = dvd_output_number > 0 and number_of_reels == 1
        multi_dvd = dvd_output_number > 0 and number_of_reels > 1

        for reel in order["reels"]:
            with self.mainwindow.render_lock:
                in_render = any(r["id"] == reel["id"] for r in self.mainwindow.render_batches)
            if in_render:
                continue

            new_reel_dict = self._build_reel(order, reel, single_dvd, multi_dvd)
            if new_reel_dict is None:
                continue
            discovered_reels.append(new_reel_dict)

    def _normalize_api_state(self, raw_state: Any) -> str:
        state_str = str(raw_state).strip().upper()
        if state_str in {"2", "RECORDED"}:
            return "RECORDED"
        return state_str if state_str else "RECORDED"

    def _merge_queue_reels(self, discovered_reels):
        discovered_by_id = {}
        for reel in discovered_reels:
            normalized = self._normalize_reel_dict(reel)
            discovered_by_id[normalized["id"]] = normalized
        discovered_ids = set(discovered_by_id.keys())
        preserve_states = {
            "EDITING",
            "CACHING",
            "CACHED",
            "TO_RENDER",
            "RENDERING",
            "TRIMMING",
            "REVERSING",
            "ADDING AUDIO",
            "FINISHING UP",
            "WAITING_FOR_CONVERTX",
            "DONE",
        }
        sync_keys = [
            "item_number",
            "order_number",
            "edited",
            "time_arrived",
            "video_out_dir",
            "add_music",
            "splits",
            "concat",
            "film_type",
            "version",
            "video_name",
            "file_type",
            "video_dir",
            "source_video_dir",
            "working_video_dir",
            "title",
            "subtitle",
            "qc_data",
            "increase_fps",
            "single_dvd",
            "multi_dvd",
            "has_sound",
            "prep_state",
            "prep_error",
            "pre_reverse_required",
            "pre_reversed",
        ]
        removed_count = 0
        added_count = 0
        updated_count = 0

        with self.mainwindow.queue_lock:
            existing_by_id = {reel["id"]: reel for reel in self.mainwindow.queue_batches}
            for reel_id, fresh in discovered_by_id.items():
                existing = existing_by_id.get(reel_id)
                if existing is None:
                    self.mainwindow.queue_batches.append(self._normalize_reel_dict(fresh))
                    added_count += 1
                    continue
                self._normalize_reel_dict(existing)
                for key in sync_keys:
                    if key in fresh:
                        if key in ("prep_state", "prep_error"):
                            continue
                        existing[key] = fresh[key]
                self._normalize_reel_dict(existing)
                source_is_avi = str(existing.get("source_video_dir", "")).lower().endswith(".avi")
                requires_pre_reverse = bool(existing.get("pre_reverse_required", False))
                working_all_exist = self._all_split_working_files_exist(existing)
                if (source_is_avi or requires_pre_reverse) and not working_all_exist and existing.get("prep_state") in {"FAILED", "READY"}:
                    existing["prep_state"] = "TO_PREP"
                    existing.pop("prep_error", None)
                if existing.get("prep_state") not in {"PREPARING", "READY"}:
                    existing["prep_state"] = fresh.get("prep_state", existing.get("prep_state"))
                    if "prep_error" in fresh:
                        existing["prep_error"] = fresh["prep_error"]
                if existing.get("state") not in preserve_states:
                    existing["state"] = fresh["state"]
                updated_count += 1

            keep_batches = []
            for reel in self.mainwindow.queue_batches:
                reel_id = reel["id"]
                if reel_id in discovered_ids:
                    keep_batches.append(reel)
                    continue
                if reel.get("state") in preserve_states:
                    keep_batches.append(reel)
                    continue
                removed_count += 1
            self.mainwindow.queue_batches = keep_batches
            state_counts = {}
            for reel in self.mainwindow.queue_batches:
                state = str(reel.get("state", "UNKNOWN"))
                state_counts[state] = state_counts.get(state, 0) + 1
            prep_counts = {}
            for reel in self.mainwindow.queue_batches:
                prep_state = str(reel.get("prep_state", "UNKNOWN"))
                prep_counts[prep_state] = prep_counts.get(prep_state, 0) + 1
        print(
            f"queue refresh: discovered={len(discovered_reels)} added={added_count} "
            f"updated={updated_count} removed={removed_count}"
        )
        print(f"queue states: {state_counts}")
        print(f"queue prep states: {prep_counts}")

    def _normalize_reel_dict(self, reel: Dict[str, Any]) -> Dict[str, Any]:
        if "splits" not in reel:
            reel["splits"] = 0
        try:
            reel["splits"] = int(reel.get("splits") or 0)
        except (TypeError, ValueError):
            reel["splits"] = 0

        reel.setdefault("qc_data", [])
        reel.setdefault("preview_loaded", False)
        reel.setdefault("add_music", True)
        reel.setdefault("concat", reel["splits"] > 0)
        reel.setdefault("increase_fps", False)
        reel.setdefault("title", "")
        reel.setdefault("subtitle", "")
        reel.setdefault("pre_reverse_required", False)
        reel.setdefault("pre_reversed", False)
        reel.setdefault("split_match_suggestions", {})
        return reel

    def _all_split_working_files_exist(self, reel: Dict[str, Any]) -> bool:
        working_base = reel.get("working_video_dir")
        if not working_base:
            return False
        splits = int(reel.get("splits") or 0)
        if splits <= 0:
            return os.path.exists(working_base)
        for split in range(splits + 1):
            split_no = split + 1
            split_working = replace_split_token(working_base, split_no)
            if not os.path.exists(split_working):
                return False
        return True

    def _build_reel(
        self, order_payload: Dict[str, Any], reel_payload: Dict[str, Any], single_dvd: bool, multi_dvd: bool
    ) -> Optional[Dict[str, Any]]:
        order_number = order_payload["order"]["order_number"]
        splits = reel_payload["splits"] or 0
        active_version = self._get_active_version(reel_payload)
        if active_version is None:
            print("reel had no active version")
            print(reel_payload)
            return None

        qc_data = reel_payload.get("comments", [])
        has_mov = any(comment.get("content_int") == 9 for comment in qc_data)
        has_reverse_comment = any(comment.get("content_int") == 8 for comment in qc_data)
        for comment in qc_data:
            comment["added_in_reeltug"] = False

        video_name_start = (
            f"{order_number} {reel_payload['film_type']} Reel {reel_payload['item_number']}V{active_version}"
        )
        input_folder = os.path.join(CINE_EDITING_DIR, order_number)
        video_name, file_type, video_dir = self._find_video_file(input_folder, video_name_start, has_mov)
        if not video_dir or not os.path.exists(video_dir):
            print("video dir not found")
            return None

        reel = ReelBatch(
            id=reel_payload["id"],
            item_number=reel_payload["item_number"],
            order_number=order_number,
            edited=reel_payload["edited"],
            state=self._normalize_api_state(reel_payload.get("state")),
            time_arrived=order_payload["order"]["time_arrived"],
            video_out_dir=os.path.join(TRANSFERRING_DIRECTORY, order_number, "CINE"),
            add_music=reel_payload["music"],
            splits=splits,
            concat=splits > 0,
            film_type=reel_payload["film_type"],
            version=active_version,
            video_name=video_name,
            file_type=file_type,
            video_dir=video_dir,
            title=reel_payload.get("title") or "",
            subtitle=reel_payload.get("subtitle") or "",
            qc_data=qc_data,
            preview_loaded=False,
            increase_fps=False,
            single_dvd=single_dvd,
            multi_dvd=multi_dvd,
        )
        reel_dict = reel.to_dict()
        reel_dict["has_sound"] = has_mov
        reel_dict["source_video_dir"] = video_dir
        reel_dict["pre_reverse_required"] = has_reverse_comment
        reel_dict["pre_reversed"] = False
        reel_dict["split_match_suggestions"] = {}
        should_preprocess = str(file_type).lower() == ".avi" or has_reverse_comment
        if should_preprocess:
            preprocess_dir = os.path.join(TEMP_VIDEO_PROCESSING_DIR, "preprocess", str(reel_payload["id"]))
            source_base_name = os.path.splitext(os.path.basename(video_dir))[0]
            suffix = "-PREVREV.mov" if has_reverse_comment else "-WORK.mov"
            reel_dict["working_video_dir"] = os.path.join(preprocess_dir, f"{source_base_name}{suffix}")
            if self._all_split_working_files_exist(reel_dict):
                working_path = reel_dict["working_video_dir"]
                reel_dict["video_dir"] = working_path
                reel_dict["video_name"] = os.path.basename(working_path)
                reel_dict["file_type"] = ".mov"
                reel_dict["prep_state"] = "READY"
                reel_dict["pre_reversed"] = has_reverse_comment
            else:
                reel_dict["prep_state"] = "TO_PREP"
        else:
            reel_dict["working_video_dir"] = video_dir
            reel_dict["prep_state"] = "READY"
        return reel_dict

    def _get_active_version(self, reel_payload: Dict[str, Any]) -> Optional[int]:
        for version in reel_payload.get("versions", []):
            if version.get("status") is True:
                return version.get("version_number")
        return None

    def _find_video_file(self, input_folder: str, video_name_start: str, has_mov: bool) -> Tuple[str, str, str]:
        extensions = [".mov", ".avi"]
        for extension in extensions:
            for root, _, files in os.walk(input_folder):
                for file_name in files:
                    if not file_name.startswith(video_name_start):
                        continue
                    if not file_name.endswith(extension):
                        continue
                    path = os.path.join(root, file_name)
                    if "SP" in file_name:
                        path = replace_split_token(path, 1)
                    return file_name, extension, path
        return "", extensions[0], ""

    def get_output_number(self, order, meta_label):
        output_format = "CINE"
        order_items = [item for item in order["order_items"] if output_format.lower() in item["name"].lower()]
        if not order_items:
            return 0

        metas = []
        for item in order_items:
            for meta in item["order_item_meta"]:
                if meta_label.lower() in meta["label"].lower():
                    metas.append(meta)
        if not metas:
            return 0

        number_items = [meta["number_items"] for meta in metas if meta["number_items"] is not None]
        return max(number_items) if number_items else 0
