import threading
from time import sleep, time

import cv2
import vlc
from PyQt5 import QtCore, QtWidgets, uic
from PyQt5.QtCore import Qt, pyqtSignal, QSettings
from PyQt5.QtWidgets import QFileDialog, QLabel, QTableWidgetItem, QMessageBox

from api import MakeRequest
from labelpreviewend import LabelPreviewEnd
from labelpreviewstart import LabelPreviewStart
from path_utils import replace_split_token
from preprocess_handler import PreprocessHandler
from preview_handler import PreviewHandler
from ui.queue_window import QueueWindow
from ui.render_window import RenderWindow
from ui.resources import ffmpeg_path, ffprobe_path, gui_main_path, gui_settings_path
from ui.workers import PreviewThread, workerSignals
from video_loader import VideoLoader


class SettingsWindow(QtWidgets.QMainWindow):
    def __init__(self, mainwindow):
        super(SettingsWindow, self).__init__()
        uic.loadUi(gui_settings_path, self)
        self.mainwindow = mainwindow
        self.settings = QSettings("teamtug", "reelTug2")
        self.video_out_dir = self.settings.value("video_out_dir", None, str)
        self.mainwindow.spinbox_start_interval = self.settings.value("spinbox_start_interval", 8, int)
        self.mainwindow.spinbox_end_interval = self.settings.value("spinbox_end_interval", 8, int)
        self.mainwindow.video_out_dir = self.video_out_dir
        self.line_edit_video_dir.setText(self.video_out_dir)
        self.output_formats = [".mp4, .mov"]
        self.combo_box_output_format.addItem(".mp4")
        self.combo_box_output_format.addItem(".mov")
        self.button_select_out_dir.clicked.connect(self.select_video_out_dir)

    def select_video_out_dir(self):
        self.video_out_dir = QFileDialog.getExistingDirectory(self, "Select folder", r"Q:\6 - Cine Editing\2022-07-29\todo")
        if not self.video_out_dir:
            return
        self.line_edit_video_dir.setText(self.video_out_dir)
        self.settings.setValue("video_out_dir", self.video_out_dir)
        self.mainwindow.video_out_dir = self.video_out_dir


class MainWindow(QtWidgets.QMainWindow):
    update_reel_table = pyqtSignal(int, int, str)
    signal_start_preview_manager = pyqtSignal()
    signal_start_preprocess_manager = pyqtSignal()
    signal_previews_loaded = pyqtSignal(int)
    signal_preview_ready = pyqtSignal(int)
    signal_reels_in_render = pyqtSignal(int)

    def __init__(self):
        super(MainWindow, self).__init__()
        uic.loadUi(gui_main_path, self)
        self.showMaximized()

        self.settings = QSettings("teamtug", "reelTug2")
        table_order_reels_modify = self.table_order_reels.horizontalHeader()
        table_order_reels_modify.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        table_order_reels_modify.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        table_order_reels_modify.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.table_order_reels.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

        self.req = MakeRequest()
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path

        self.fresh_load = True
        self.video_loaded = False
        self.queue_connected = False
        self.caching_previews = False
        self.preprocessing_queue = False
        self.stop_background_workers = False

        self.queue_lock = threading.RLock()
        self.render_lock = threading.RLock()
        self.queue_batches = []
        self.render_batches = []
        self.cached_previews = []

        self.active_reel = {}
        self.previews_loaded = 0
        self.current_split = 0

        self.preview_start_position = 0
        self.preview_end_position = 0
        self.previews_loading = False
        self.preview_start_trim_set = False
        self.preview_start_trim_frame = ""
        self.preview_end_trim_set = False
        self.preview_end_trim_frame = ""
        self.end_loaded_once = False
        self.start_loaded_once = False

        self.end_interval = 8
        self.start_interval = 8
        self.accepted_video_formats = [".mp4", ".mov", ".avi"]

        # Keep libVLC stderr noise out of app stdout (plugin cache and audio backend warnings).
        self.vlc_instance = vlc.Instance("--quiet", "--verbose=-1")
        self.mediaplayer = self.vlc_instance.media_player_new()
        self.mediaplayer.set_hwnd(int(self.video_frame.winId()))
        self.video_slider.setToolTip("Position")
        self.video_slider.setMaximum(1000)
        self.video_slider.sliderMoved.connect(self.video_slider_position)
        self.video_slider.setStyleSheet(
            """
            QSlider::handle {
                height: 40px;
                background: green;
                margin: 0 -4px;
            }
            """
        )

        self.labels_start = {}
        for label in [self.sf0, self.sf1, self.sf2, self.sf3, self.sf4, self.sf5, self.sf6, self.sf7, self.sf8, self.sf9]:
            label.label_signal_start.connect(self.start_previews_highlight_mgmt)
            self.labels_start[str(id(label))] = label
        self.labels_end = {}
        for label in [self.ef0, self.ef1, self.ef2, self.ef3, self.ef4, self.ef5, self.ef6, self.ef7, self.ef8, self.ef9]:
            label.label_signal_end.connect(self.end_previews_highlight_mgmt)
            self.labels_end[str(id(label))] = label

        self.menu_open_settings.triggered.connect(self.open_settings)
        self.button_queue.clicked.connect(self.open_queue_window)
        self.button_render_queue.clicked.connect(self.open_render_queue_window)
        self.button_prev_start_next.clicked.connect(self.preview_start_next_load)
        self.button_prev_start_prev.clicked.connect(self.preview_start_prev_load)
        if hasattr(self, "button_prev_start_next_big"):
            self.button_prev_start_next_big.clicked.connect(self.preview_start_next_load_big)
        if hasattr(self, "button_prev_start_prev_big"):
            self.button_prev_start_prev_big.clicked.connect(self.preview_start_prev_load_big)
        self.button_prev_end_next.clicked.connect(self.preview_end_next_load)
        self.button_prev_end_prev.clicked.connect(self.preview_end_prev_load)
        if hasattr(self, "button_prev_end_next_big"):
            self.button_prev_end_next_big.clicked.connect(self.preview_end_next_load_big)
        if hasattr(self, "button_prev_end_prev_big"):
            self.button_prev_end_prev_big.clicked.connect(self.preview_end_prev_load_big)
        self.button_next_video.clicked.connect(self.next_split)
        self.button_prev_video.clicked.connect(self.previous_split)
        self.button_add_to_render.clicked.connect(self.add_to_render)
        self.button_next_order.clicked.connect(self.next_order)
        self.button_play.clicked.connect(self.video_toggle_playback)

        self.workerSignals = workerSignals()
        self.signal_start_preview_manager.connect(self.start_preview_manager_thread)
        self.signal_start_preprocess_manager.connect(self.start_preprocess_manager_thread)
        self.signal_previews_loaded.connect(self.gui_update_previews_loaded)
        self.signal_preview_ready.connect(self.on_preview_ready)
        self.signal_reels_in_render.connect(self.gui_update_reels_in_render)

        self.preview_threadpool = QtCore.QThreadPool()
        self.queue_window = QueueWindow(self)
        self.render_window = RenderWindow(self)
        self.settings_window = SettingsWindow(self)
        self.split_match_status_label = QLabel("Split Match: N/A")
        self.statusBar().addPermanentWidget(self.split_match_status_label)
        self.signal_start_preprocess_manager.emit()
        self.refresh_reels_in_render_count()

    def closeEvent(self, event):
        self.stop_background_workers = True
        super().closeEvent(event)

    def fetch_previews_thread(self, reel_id, set_preview):
        preview_thread = PreviewThread(self.fetch_previews, self, self.workerSignals, reel_id, set_preview)
        self.preview_threadpool.start(preview_thread)

    def fetch_previews(self, main_window, worker_signals, reel_id, set_preview):
        previews_worker = PreviewHandler(main_window, worker_signals)
        previews_worker.fetch_previews(reel_id, set_preview)
        if set_preview:
            self.signal_preview_ready.emit(reel_id)

    def on_preview_ready(self, reel_id):
        if not isinstance(self.active_reel, dict):
            return
        if self.active_reel.get("id") != reel_id:
            return
        if "preview_data" not in self.active_reel:
            return
        target_split = self.current_split if self.current_split in self.active_reel["preview_data"] else 0
        if target_split not in self.active_reel["preview_data"]:
            return
        try:
            self.set_previews(reel_id, target_split)
        except KeyError as exc:
            print(f"[on_preview_ready] failed to set previews for reel {reel_id} split {target_split}: {exc}")

    def start_preview_manager_thread(self):
        if self.caching_previews:
            return
        self.caching_previews = True
        preview_thread = PreviewThread(self.start_preview_manager, self, self.workerSignals, "arg3", "arg4")
        self.preview_threadpool.start(preview_thread)

    def start_preview_manager(self, mainwindow, worker_signals, arg3, arg4):
        previews_worker = PreviewHandler(mainwindow, worker_signals)
        previews_worker.preview_manager()

    def start_preprocess_manager_thread(self):
        if self.preprocessing_queue:
            return
        self.preprocessing_queue = True
        preprocess_thread = PreviewThread(self.start_preprocess_manager, self, self.workerSignals, "arg3", "arg4")
        self.preview_threadpool.start(preprocess_thread)

    def start_preprocess_manager(self, mainwindow, worker_signals, arg3, arg4):
        preprocess_worker = PreprocessHandler(mainwindow)
        preprocess_worker.preprocess_manager()

    def set_previews(self, reel_id, split):
        previews_worker = PreviewHandler(self, self.workerSignals)
        previews_worker.set_previews(reel_id, split)

    def open_queue_window(self):
        if self.queue_window.isVisible():
            self.queue_window.setWindowState(
                self.render_window.windowState() & ~QtCore.Qt.WindowMinimized | QtCore.Qt.WindowActive
            )
            self.queue_window.activateWindow()
        else:
            self.queue_window.show()

    def open_render_queue_window(self):
        self.render_window.show()
        if self.render_window.isVisible():
            self.render_window.setWindowState(
                self.render_window.windowState() & ~QtCore.Qt.WindowMinimized | QtCore.Qt.WindowActive
            )
            self.render_window.activateWindow()

    def open_settings(self):
        self.settings_window.show()

    def reset_states(self):
        self.video_loaded = False
        self.fresh_load = True
        self.previews_loaded = False
        self.current_split = 0
        self.preview_start_position = 0
        self.preview_end_position = 0
        self.preview_start_trim_set = False
        self.preview_start_trim_frame = ""
        self.preview_end_trim_set = False
        self.preview_end_trim_frame = ""
        self.end_loaded_once = False
        self.start_loaded_once = False
        self.active_reel = {}
        self.previews_remove_all_highlights()
        self.previews_remove_all_previews()
        self.mediaplayer.stop()
        while self.table_order_reels.rowCount() > 0:
            self.table_order_reels.removeRow(0)
        self.table_order_reels.setRowCount(0)
        self.cb_1.setChecked(False)
        self.cb_2.setChecked(False)
        self.cb_4.setChecked(False)
        self.cb_5.setChecked(False)
        self.cb_6.setChecked(False)
        self.cb_7.setChecked(False)
        self._update_split_match_status()

    def next_order(self):
        self.reset_states()
        with self.queue_lock:
            loaded_previews_available = [d for d in self.queue_batches if d["state"] == "CACHED"]
        if len(loaded_previews_available) > 0:
            next_cached = loaded_previews_available[-1]
            next_cached["state"] = "EDITING"
            cached_id = next_cached["id"]
            with self.queue_lock:
                self.active_reel = [d for d in self.queue_batches if d["id"] == cached_id][0]
            preview_worker = PreviewHandler(self, self.workerSignals)
            self.previews_loading = True
            try:
                preview_worker.set_previews(cached_id, 0)
            except KeyError as e:
                self.previews_loading = False
                next_cached["state"] = "CACHED"
                if "preview_data" in next_cached:
                    del next_cached["preview_data"]
                self.pop_up_message(
                    f"Error loading previews: missing preview frame. Skipping this reel and moving to next. Error: {str(e)}",
                    "Preview Loading Error",
                )
                self.next_order()
                return
        else:
            with self.queue_lock:
                ready_reels = [
                    d for d in self.queue_batches
                    if d["state"] == "RECORDED" and d.get("prep_state", "READY") == "READY"
                ]
                waiting_prep = [
                    d for d in self.queue_batches
                    if d["state"] == "RECORDED" and d.get("prep_state", "READY") != "READY"
                ]
            if len(ready_reels) == 0:
                if len(waiting_prep) > 0:
                    self.pop_up_message("No reels are ready yet. Waiting for preprocess to finish.", "Preprocessing")
                    return
                self.pop_up_message("No more reels to edit.", "Out of reels")
                return
            self.active_reel = ready_reels[-1]
            self.active_reel["state"] = "EDITING"
            self.previews_loading = True
            self.fetch_previews_thread(self.active_reel["id"], True)
        self.set_new_video()
        if "preview_data" in self.active_reel and self.current_split in self.active_reel["preview_data"]:
            self.set_previews(self.active_reel["id"], self.current_split)
        self.load_qc_table()
        self._load_split_settings_ui(self.current_split)
        for split in range(self.active_reel["splits"] + 1):
            self.table_order_reels.insertRow(split)
            self.table_order_reels.setItem(split, 0, QTableWidgetItem(str(self.active_reel["order_number"])))
            self.table_order_reels.setItem(split, 1, QTableWidgetItem(str(self.active_reel["item_number"])))
            self.table_order_reels.setItem(split, 2, QTableWidgetItem(str(split)))
            title = self.active_reel["title"] + " " + self.active_reel["subtitle"]
            self.table_order_reels.setItem(split, 3, QTableWidgetItem(title))
        self.table_order_reels.selectRow(0)
        self.video_loaded = True
        self.fresh_load = False
        self._update_split_match_status(self.current_split)

    def load_reel(self, reel):
        self.reset_states()
        self.active_reel = reel
        self.active_reel["state"] = "EDITING"
        self.previews_loading = True
        self.fetch_previews_thread(self.active_reel["id"], True)
        self.set_new_video()
        if "preview_data" in self.active_reel and self.current_split in self.active_reel["preview_data"]:
            self.set_previews(self.active_reel["id"], self.current_split)
        self.load_qc_table()
        self._load_split_settings_ui(self.current_split)
        for split in range(self.active_reel["splits"] + 1):
            self.table_order_reels.insertRow(split)
            self.table_order_reels.setItem(split, 0, QTableWidgetItem(str(self.active_reel["order_number"])))
            self.table_order_reels.setItem(split, 1, QTableWidgetItem(str(self.active_reel["item_number"])))
            self.table_order_reels.setItem(split, 2, QTableWidgetItem(str(split + 1)))
            title = self.active_reel["title"] + " " + self.active_reel["subtitle"]
            self.table_order_reels.setItem(split, 3, QTableWidgetItem(title))
        self.table_order_reels.selectRow(0)
        self.video_loaded = True
        self.fresh_load = False
        self._update_split_match_status(self.current_split)

    def set_new_video(self):
        self.video_slider.setSliderPosition(0)
        video_dir = self.active_reel["video_dir"]
        if self.active_reel["splits"] > 0:
            split = self.current_split + 1
            video_dir = replace_split_token(video_dir, split)
        self.line_override_fps.setText(str(self.choose_fps(self.active_reel["film_type"])))
        load_video = VideoLoader(self)
        load_video.open_video(video_dir)
        self.video_loaded = True
        while self.active_reel.get("preview_loaded") is False:
            QtWidgets.QApplication.processEvents()
            sleep(0.05)

    def next_split(self):
        if self.fresh_load or self.active_reel["splits"] == 0 or self.video_loaded is False:
            return
        if self.current_split == self.active_reel["splits"]:
            return
        self.save_highlight_config()
        self.previews_remove_all_highlights()
        self.current_split += 1
        if self.active_reel["preview_loaded"] is False:
            return
        self.table_order_reels.selectRow(self.current_split)
        self.set_previews(self.active_reel["id"], self.current_split)
        self.set_new_video()
        self._load_split_settings_ui(self.current_split)
        if self.active_reel[self.current_split]["edited"] is True:
            self.load_highlight_config()
        self._update_split_match_status(self.current_split)

    def previous_split(self):
        if self.fresh_load or self.active_reel["splits"] == 0 or self.video_loaded is False:
            return
        self.save_highlight_config()
        self.previews_remove_all_highlights()
        self.current_split -= 1
        if self.current_split < 0:
            self.current_split = 0
            return
        if self.active_reel["preview_loaded"] is False:
            return
        self.table_order_reels.selectRow(self.current_split)
        self.set_previews(self.active_reel["id"], self.current_split)
        self.set_new_video()
        self._load_split_settings_ui(self.current_split)
        if self.active_reel[self.current_split]["edited"] is True:
            self.load_highlight_config()
        self._update_split_match_status(self.current_split)

    def next_video(self):
        "TO DO"

    def add_to_render(self):
        if self.fresh_load:
            self.next_order()
            return
        if not isinstance(self.active_reel, dict) or "id" not in self.active_reel:
            print(f"[add_to_render] invalid active_reel payload: type={type(self.active_reel)} value={self.active_reel}")
            self.next_order()
            return
        try:
            splits = int(self.active_reel.get("splits") or 0)
        except (TypeError, ValueError):
            splits = 0
        self.active_reel["splits"] = splits
        if splits > 0:
            current_split = self.current_split
            if current_split != splits:
                self.pop_up_message("More splits to edit?", "Edit all splits")
                return
        if self.video_loaded is False:
            self.pop_up_message("No videos loaded, load a batch to begin editing.", "No batch loaded.")
            return

        self.save_highlight_config()
        for qc_comment in self.active_reel["qc_data"]:
            if qc_comment["added_in_reeltug"] is True:
                new_comment = {"internal": qc_comment["internal"], "reels": [qc_comment["reel_id"]]}
                if "content_int" in qc_comment:
                    new_comment["comments"] = [qc_comment["content_int"]]
                else:
                    new_comment["comments"] = [qc_comment["content_str"]]
                if qc_comment["added"] is True:
                    self.req.make_post("/cine/cine-tug/add-comment", new_comment)
                else:
                    delete_comment = {"reel_comments": [qc_comment["id"]]}
                    self.req.make_post("/cine/cine-tug/delete-comment", delete_comment)
        self.active_reel["add_music"] = self.checkbox_add_music.checkState() == 2
        reel_id = self.active_reel["id"]
        with self.queue_lock:
            reel = [d for d in self.queue_batches if d["id"] == reel_id][0]
            reel["state"] = "TO_RENDER"
            reel["preview_loaded"] = False
            if "preview_data" in reel:
                del reel["preview_data"]
            previews_loaded = len([d for d in self.queue_batches if d.get("preview_loaded") is True and d.get("state") != "TO_RENDER"])
        self.signal_previews_loaded.emit(previews_loaded)
        with self.render_lock:
            self.render_batches.append(self.active_reel)
        self.refresh_reels_in_render_count()
        self.render_window.update_render_table()
        self.reset_states()
        if self.check_auto_next.checkState() == 2:
            self.next_order()
        self.queue_window.update_queue_table_gui()

    def load_previews_thread(self):
        "TO DO"

    def load_previews(self):
        "TO DO"

    def preview_start_next_load(self):
        if self.video_loaded is False or self.previews_loading is True:
            return
        PreviewHandler(self, self.workerSignals).change_start_previews("next", self.start_loaded_once)

    def preview_start_prev_load(self):
        if self.video_loaded is False or self.previews_loading is True:
            return
        PreviewHandler(self, self.workerSignals).change_start_previews("prev", self.start_loaded_once)

    def preview_start_next_load_big(self):
        if self.video_loaded is False or self.previews_loading is True:
            return
        PreviewHandler(self, self.workerSignals).change_start_previews("next_big", self.start_loaded_once)

    def preview_start_prev_load_big(self):
        if self.video_loaded is False or self.previews_loading is True:
            return
        PreviewHandler(self, self.workerSignals).change_start_previews("prev_big", self.start_loaded_once)

    def preview_end_next_load(self):
        if self.video_loaded is False or self.previews_loading is True:
            return
        PreviewHandler(self, self.workerSignals).change_end_previews("next", self.end_loaded_once)

    def preview_end_prev_load(self):
        if self.video_loaded is False or self.previews_loading is True:
            return
        PreviewHandler(self, self.workerSignals).change_end_previews("prev", self.end_loaded_once)

    def preview_end_next_load_big(self):
        if self.video_loaded is False or self.previews_loading is True:
            return
        PreviewHandler(self, self.workerSignals).change_end_previews("next_big", self.end_loaded_once)

    def preview_end_prev_load_big(self):
        if self.video_loaded is False or self.previews_loading is True:
            return
        PreviewHandler(self, self.workerSignals).change_end_previews("prev_big", self.end_loaded_once)

    def start_previews_highlight_mgmt(self, label_id):
        for k, v in self.labels_start.items():
            if k != label_id:
                v.highlighted = False
                v.setStyleSheet("background-color:  rgba(0, 0, 0, 0)")

    def end_previews_highlight_mgmt(self, label_id):
        for k, v in self.labels_end.items():
            if k != label_id:
                v.highlighted = False
                v.setStyleSheet("background-color:  rgba(0, 0, 0, 0)")

    def previews_remove_all_highlights(self):
        for _, v in self.labels_start.items():
            v.highlighted = False
            v.setStyleSheet("background-color:  rgba(0, 0, 0, 0)")
        for _, v in self.labels_end.items():
            v.highlighted = False
            v.setStyleSheet("background-color:  rgba(0, 0, 0, 0)")

    def previews_remove_all_previews(self):
        for _, v in self.labels_start.items():
            v.clear()
        for _, v in self.labels_end.items():
            v.clear()

    def get_frames_from_highlight(self):
        highlighted_dict = {}
        start_trim_frame = ""
        end_trim_frame = ""
        for i in range(10):
            highlighted_dict[f"sf{i}"] = getattr(self, f"sf{i}").highlighted
            highlighted_dict[f"ef{i}"] = getattr(self, f"ef{i}").highlighted
        for key, value in highlighted_dict.items():
            if value is True:
                if key.startswith("sf"):
                    start_trim_frame = key
                elif key.startswith("ef"):
                    end_trim_frame = key
        if start_trim_frame == "":
            start_frame = 1
        else:
            start_frame = self.active_reel["preview_data"][self.current_split]["start_gui_frame_data"][start_trim_frame]
            start_frame = int(start_frame.replace("start_frame", ""))
        if end_trim_frame == "":
            video_dir = self.active_reel["video_dir"]
            if self.current_split > 0:
                video_dir = replace_split_token(video_dir, self.current_split + 1)
            video = cv2.VideoCapture(video_dir)
            end_frame = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
        else:
            end_frame = self.active_reel["preview_data"][self.current_split]["end_gui_frame_data"][end_trim_frame]
            end_frame = int(end_frame.replace("end_frame", ""))
        return start_frame, end_frame, start_trim_frame, end_trim_frame

    def save_highlight_config(self):
        current_split = self.current_split
        reel_data = self.active_reel
        start_frame, end_frame, start_trim_frame, end_trim_frame = self.get_frames_from_highlight()
        if reel_data[current_split]["edited"] is True:
            if start_trim_frame == "":
                start_trim_frame = reel_data["highlight_data"][current_split]["start_trim_frame"]
            if end_trim_frame == "":
                end_trim_frame = reel_data["highlight_data"][current_split]["end_trim_frame"]
        else:
            if start_trim_frame == "":
                start_trim_frame = "sf0"
            if end_trim_frame == "":
                end_trim_frame = "ef0"
        if "highlight_data" not in reel_data:
            reel_data["highlight_data"] = {}
        reel_data["highlight_data"][current_split] = {
            "start_trim_frame": start_trim_frame,
            "end_trim_frame": end_trim_frame,
            "preview_start_position": self.preview_start_position,
            "preview_end_position": self.preview_end_position,
        }
        if "trim_data" not in reel_data:
            reel_data["trim_data"] = {}
        reel_data["trim_data"][current_split] = {"start_frame": start_frame, "end_frame": end_frame}
        if current_split not in self.active_reel:
            self.active_reel[current_split] = {}
        split_reverse = self.checkbox_reverse.checkState() == 2
        self.active_reel[current_split]["reverse"] = split_reverse
        self.active_reel[current_split]["reverse_set_by_operator"] = (split_reverse != self._default_reverse_for_reel())
        if self.line_override_fps.text() != str(self.choose_fps(self.active_reel["film_type"])):
            self.active_reel[current_split]["fps"] = int(self.line_override_fps.text())
        self.active_reel[current_split]["edited"] = True
        # FPS interpolation UI has been removed; keep behavior explicitly disabled.
        self.active_reel["increase_fps"] = False
        self.active_reel["concat"] = self.checkbox_combine.checkState() == 2
        self.add_new_qc_comments()
        self._update_split_match_status(current_split)

    def _default_reverse_for_reel(self):
        if self.active_reel.get("pre_reversed"):
            return False
        for qc_comment in self.active_reel.get("qc_data", []):
            if qc_comment.get("content_int") == 8:
                return True
        return False

    def _load_split_settings_ui(self, split):
        split_state = self.active_reel.get(split, {})
        reverse_value = split_state.get("reverse")
        if reverse_value is None:
            reverse_value = self._default_reverse_for_reel()
        self.checkbox_reverse.setChecked(bool(reverse_value))

        fps_value = split_state.get("fps")
        if fps_value is None:
            fps_value = self.choose_fps(self.active_reel["film_type"])
        self.line_override_fps.setText(str(fps_value))

    def load_highlight_config(self):
        start_trim_frame = self.active_reel["highlight_data"][self.current_split]["start_trim_frame"]
        end_trim_frame = self.active_reel["highlight_data"][self.current_split]["end_trim_frame"]
        getattr(self, start_trim_frame).setStyleSheet("border: 3px solid blue;")
        getattr(self, end_trim_frame).setStyleSheet("border: 3px solid blue;")

    def _split_match_suggestion_for(self, split):
        suggestions = self.active_reel.get("split_match_suggestions", {})
        suggestion = suggestions.get(split)
        if suggestion is None:
            suggestion = suggestions.get(str(split))
        if isinstance(suggestion, dict):
            return suggestion
        return None

    def _update_split_match_status(self, split=None):
        if not hasattr(self, "split_match_status_label"):
            return
        if not isinstance(self.active_reel, dict) or "id" not in self.active_reel:
            self.split_match_status_label.setText("Split Match: N/A")
            return

        if split is None:
            split = self.current_split
        suggestion = self._split_match_suggestion_for(split)
        if not suggestion:
            self.split_match_status_label.setText(f"Split {split + 1}: No auto match")
            return

        conf_values = []
        for key in ("start_confidence", "end_confidence"):
            if key not in suggestion:
                continue
            try:
                conf_values.append(float(suggestion[key]))
            except (TypeError, ValueError):
                continue
        confidence_text = "unknown"
        if conf_values:
            confidence_text = f"{(sum(conf_values) / len(conf_values)):.2f}"

        split_state = self.active_reel.get(split, {})
        if split_state.get("edited"):
            mode = "Operator adjusted"
        else:
            mode = "Auto suggested"

        self.split_match_status_label.setText(
            f"Split {split + 1}: {mode} join (confidence {confidence_text})"
        )

    def video_toggle_playback(self):
        if self.mediaplayer.is_playing():
            self.mediaplayer.pause()
            self.button_play.setText("Play")
            self.isPaused = True
        else:
            self.mediaplayer.play()
            self.button_play.setText("Pause")
            self.isPaused = False

    def video_slider_position(self, position):
        self.mediaplayer.set_position(position / 1000.0)

    def gui_update_previews_loaded(self, previews_loaded):
        self.line_previews_loaded.setText(str(previews_loaded))

    def gui_update_reels_in_render(self, reels_in_render):
        if hasattr(self, "line_reels_in_render"):
            self.line_reels_in_render.setText(str(reels_in_render))

    def refresh_reels_in_render_count(self):
        with self.render_lock:
            reels_in_render = len(self.render_batches)
        self.signal_reels_in_render.emit(reels_in_render)

    def load_qc_table(self):
        self.cb_1.setChecked(False)
        self.cb_2.setChecked(False)
        self.cb_4.setChecked(False)
        self.cb_5.setChecked(False)
        self.cb_6.setChecked(False)
        self.cb_7.setChecked(False)
        self.checkbox_reverse.setChecked(False)
        self.checkbox_add_music.setChecked(True)
        self.line_qc_custom_comment.setText("")
        self.line_qc_custom_comment_external.setText("")
        self.checkbox_combine.setChecked(False)
        comment_list = [1, 2, 4, 5, 6, 7]
        str_comments = ""
        for qc_comment in self.active_reel["qc_data"]:
            if qc_comment["content_int"] == 9:
                self.pop_up_message("This reel has sound.", "Reel has sound")
                self.checkbox_add_music.setChecked(False)
            if qc_comment["content_int"] == 8 and not self.active_reel.get("pre_reversed"):
                self.checkbox_reverse.setChecked(True)
            if qc_comment["content_int"] is None:
                str_comments += qc_comment["content_str"] + "\n"
            if qc_comment["content_int"] in comment_list:
                self.convert_quality_comment(qc_comment["content_int"])
        if self.active_reel["add_music"] is False:
            self.pop_up_message(
                "Add music has been disabled for this order, correct? Ask to remove this message", "No music request"
            )
            self.checkbox_add_music.setChecked(False)
        self.line_qc_custom_comment.setText(str_comments)
        if self.line_qc_custom_comment.text() != "":
            self.pop_up_message("Custom comment on this reel", "Custom comment request")
        if self.active_reel["concat"] is True:
            self.checkbox_combine.setChecked(True)

    def choose_fps(self, film_type):
        if film_type in ("R8", "R9", 9.5):
            return 16
        if film_type in ("S8", "R16", 16):
            return 18
        return 18

    def convert_quality_comment(self, qc_comment):
        obj_name = "cb_" + str(qc_comment)
        getattr(self, obj_name).setChecked(True)

    def add_new_qc_comments(self):
        comment_list = [1, 2, 4, 5, 6, 7]
        for comment in comment_list:
            attribute = getattr(self, "cb_" + str(comment))
            qc_comment_data = self.active_reel["qc_data"]
            if attribute.isChecked() is True:
                already_commented = [qc_comment for qc_comment in qc_comment_data if qc_comment["content_int"] == comment]
                if len(already_commented) == 0:
                    self.active_reel["qc_data"].append(
                        {
                            "internal": False,
                            "reel_id": self.active_reel["id"],
                            "added_in_reeltug": True,
                            "added": True,
                            "content_int": comment,
                        }
                    )
            else:
                already_commented = [qc_comment for qc_comment in qc_comment_data if qc_comment.get("content_int") == comment]
                if len(already_commented) > 0:
                    already_commented[0]["added_in_reeltug"] = True
                    already_commented[0]["added"] = False
        if self.line_qc_custom_comment_external.text() != "":
            self.active_reel["qc_data"].append(
                {
                    "internal": False,
                    "reel_id": self.active_reel["id"],
                    "added_in_reeltug": True,
                    "added": True,
                    "content_str": self.line_qc_custom_comment_external.text(),
                }
            )

    def gui_update_reel_table(self, row, column, text):
        self.table_order_reels.setItem(row, column, QTableWidgetItem(text))

    def pop_up_message(self, text, title):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setText(text)
        msg.setWindowTitle(title)
        msg.exec_()
