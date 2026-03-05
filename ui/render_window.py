import csv
import datetime
import os
import pickle
from time import sleep

from PyQt5 import QtCore, QtWidgets, uic
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QTableWidgetItem, QMessageBox

from config import PICKLE_BACKUP_DIR
from render import ProcessVideo
from ui.resources import gui_render_path
from ui.workers import RenderThread


class RenderWindow(QtWidgets.QMainWindow):
    update_render_table_signal = pyqtSignal()
    update_progress_bar = pyqtSignal(int)

    def __init__(self, mainwindow):
        super(RenderWindow, self).__init__()
        uic.loadUi(gui_render_path, self)
        self.mainwindow = mainwindow
        self.rendering = False
        self.time_start = 0

        table_render_queue_modify = self.table_render.horizontalHeader()
        table_render_queue_modify.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        table_render_queue_modify.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        table_render_queue_modify.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        table_render_queue_modify.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        table_render_queue_modify.setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        self.table_render.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)

        self.button_render.clicked.connect(self.render)
        self.check_box_auto_render.stateChanged.connect(self.auto_render)
        self.button_recover.clicked.connect(self.crash_recover)
        self.button_remove.clicked.connect(self.remove)
        self.button_modify.clicked.connect(self.modify)
        self.button_clear_all.clicked.connect(self.clear_all)

        self.threadpool = QtCore.QThreadPool()
        self.update_render_table_signal.connect(self.update_render_table)
        self.crash_recover()

    def update_render_table(self):
        self.save_render_as_pickle(False)
        with self.mainwindow.render_lock:
            render_batches = list(self.mainwindow.render_batches)
        while self.table_render.rowCount() > 0:
            self.table_render.removeRow(0)
        self.table_render.setRowCount(0)
        for row, render_batch in enumerate(render_batches):
            self.table_render.insertRow(row)
            self.table_render.setItem(row, 0, QTableWidgetItem(str(render_batch["order_number"])))
            self.table_render.setItem(row, 1, QTableWidgetItem(str(render_batch["item_number"])))
            self.table_render.setItem(row, 2, QTableWidgetItem(str(render_batch["id"])))
            self.table_render.setItem(row, 3, QTableWidgetItem(str(render_batch["state"])))
            self.table_render.setItem(row, 4, QTableWidgetItem(str(render_batch["video_out_dir"])))
        self.mainwindow.refresh_reels_in_render_count()

    def save_render_as_pickle(self, at_render):
        batches_left = []
        if not os.path.isdir(PICKLE_BACKUP_DIR):
            os.makedirs(PICKLE_BACKUP_DIR, exist_ok=True)
        with self.mainwindow.render_lock:
            render_batches = list(self.mainwindow.render_batches)
        for batch in render_batches:
            if batch["state"] != "DONE":
                if "preview_data" in batch:
                    del batch["preview_data"]
                batches_left.append(batch)
        if at_render is False:
            pickle_path = os.path.join(PICKLE_BACKUP_DIR, "render_batches.pickle")
            pickle.dump(batches_left, open(pickle_path, "wb"))
        else:
            dt_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_filename = f"render_batches{dt_str}.pickle"
            backup_path = os.path.join(PICKLE_BACKUP_DIR, backup_filename)
            pickle.dump(batches_left, open(backup_path, "wb"))

    def crash_recover(self):
        import glob

        latest_pickle = None
        if os.path.isdir(PICKLE_BACKUP_DIR):
            pickle_files = glob.glob(os.path.join(PICKLE_BACKUP_DIR, "*.pickle"))
            if pickle_files:
                pickle_files.sort(key=os.path.getmtime, reverse=True)
                latest_pickle = pickle_files[0]

        if latest_pickle and os.path.isfile(latest_pickle):
            recovered = pickle.load(open(latest_pickle, "rb"))
            for batch in recovered:
                batch["state"] = "TO_RENDER"
            with self.mainwindow.render_lock:
                self.mainwindow.render_batches = recovered
        self.update_render_table()

    def remove(self):
        try:
            row = self.table_render.selectedItems()[0].row()
        except IndexError:
            self._err("No render batch selected, select a render batch.", "Render batch not selected")
            return

        reel_id = int(self.table_render.item(row, 2).text())
        try:
            with self.mainwindow.render_lock:
                batch = [d for d in self.mainwindow.render_batches if d["id"] == reel_id][0]
                self.mainwindow.render_batches.remove(batch)
        except IndexError:
            self._err("Could not find render batch in render batches.", "Could not find render batch")
            return
        self.update_render_table()

    def modify(self):
        if self.rendering:
            self._err("Application is currently rendering.", "Render in progress")
            return
        try:
            row = self.table_render.selectedItems()[0].row()
        except IndexError:
            self._err("No render batch selected, select a render batch.", "Render batch not selected")
            return

        reel_id = int(self.table_render.item(row, 2).text())
        with self.mainwindow.render_lock:
            matches = [d for d in self.mainwindow.render_batches if d["id"] == reel_id]
            if not matches:
                self._err("Could not find render batch in render batches.", "Could not find render batch")
                return
            reel = matches[0]
            state = str(reel.get("state", ""))
            allowed = state == "TO_RENDER" or state.startswith("FAILED")
            if not allowed:
                self._err(
                    f"Can only modify reels in TO_RENDER or FAILED state. Current state: {state}",
                    "Reel not editable",
                )
                return

        active_id = self.mainwindow.active_reel.get("id") if isinstance(self.mainwindow.active_reel, dict) else None
        if self.mainwindow.video_loaded and active_id is not None and active_id != reel_id:
            answer = QMessageBox.question(
                self,
                "Discard current edit?",
                "Another reel is currently loaded for editing. Open selected render reel instead?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        with self.mainwindow.queue_lock:
            queue_matches = [d for d in self.mainwindow.queue_batches if d["id"] == reel_id]
            if queue_matches:
                queue_reel = queue_matches[0]
                queue_reel.update(reel)
            else:
                queue_reel = reel
                self.mainwindow.queue_batches.append(queue_reel)
            queue_reel["state"] = "EDITING"

        with self.mainwindow.render_lock:
            self.mainwindow.render_batches = [d for d in self.mainwindow.render_batches if d["id"] != reel_id]

        self.mainwindow.load_reel(queue_reel)
        self.update_render_table()
        self.mainwindow.queue_window.update_queue_table_gui()
        self.mainwindow.show()
        self.mainwindow.activateWindow()
        self.close()

    def clear_all(self):
        selected_items = self.table_render.selectedItems()
        if not selected_items:
            self._err("No render batch selected, select one or more render batches.", "Render batch not selected")
            return
        selected_rows = {item.row() for item in selected_items}
        ids_to_remove = []
        for row in selected_rows:
            id_item = self.table_render.item(row, 2)
            if id_item:
                try:
                    ids_to_remove.append(int(id_item.text()))
                except ValueError:
                    continue
        with self.mainwindow.render_lock:
            self.mainwindow.render_batches = [
                batch for batch in self.mainwindow.render_batches if batch["id"] not in ids_to_remove
            ]
        self.update_render_table()

    def render(self):
        if self.rendering:
            self._err("Application is currently rendering.", "Render in progress")
            return
        selected_items = self.table_render.selectedItems()
        if not selected_items:
            self._err("No render batch selected, select a render batch.", "Render batch not selected")
            return
        selected_rows = {item.row() for item in selected_items}
        ids_to_render = []
        for row in selected_rows:
            id_item = self.table_render.item(row, 2)
            if id_item:
                try:
                    ids_to_render.append(int(id_item.text()))
                except ValueError:
                    continue
        if ids_to_render:
            reel_id = ids_to_render[0]
            with self.mainwindow.render_lock:
                reel = [d for d in self.mainwindow.render_batches if d["id"] == reel_id][0]
            self.start_render_single_video_thread(reel)

    def auto_render(self):
        if self.check_box_auto_render.checkState() != QtCore.Qt.Checked:
            return
        if self.rendering:
            self._err("Application is currently rendering.", "Render in progress")
            return
        self.save_render_as_pickle(True)
        self.start_auto_render_thread()

    def start_auto_render_thread(self):
        self.rendering = True
        self.progress_bar_render.setValue(0)
        render_worker = RenderThread(self.auto_render_thread, self, self.mainwindow, "arg3")
        self.threadpool.start(render_worker)

    def on_render_thread_finished(self):
        self.save_table_to_csv(self.table_render)

    def save_table_to_csv(self, table):
        with open("./table_data.csv", "w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["Order Number", "Item Number", "ID", "State", "Video Out Dir"])
            for row in range(table.rowCount()):
                row_data = []
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    row_data.append(item.text() if item and item.text() else "")
                writer.writerow(row_data)

    def auto_render_thread(self, render_window, mainwindow, arg3):
        while not getattr(mainwindow, "stop_background_workers", False):
            try:
                with self.mainwindow.render_lock:
                    reels = [d for d in self.mainwindow.render_batches if d.get("state") == "TO_RENDER"]
                if len(reels) == 0:
                    sleep(1)
                    continue
                for reel in reels:
                    reel["state"] = "RENDERING"
                    try:
                        self.render_single_video(render_window, mainwindow, reel)
                    except Exception as e:
                        reel["state"] = f"FAILED: {e}"
                        print(f"[auto_render] reel {reel.get('id')} failed: {e}")
            except Exception as exc:
                print(f"[auto_render] loop error: {exc}")
                sleep(1)

    def start_render_single_video_thread(self, reel):
        self.render_state = 1
        self.progress_bar_render.setValue(0)
        render_worker = RenderThread(self.render_single_video, self, self.mainwindow, reel)
        self.threadpool.start(render_worker)

    def render_single_video(self, render_window, mainwindow, reel):
        process_video = ProcessVideo(render_window, mainwindow)
        process_video.process_video(reel)
        self.progress_bar_render.setValue(100)

    def _err(self, text, title):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setText(text)
        msg.setWindowTitle(title)
        msg.exec_()
