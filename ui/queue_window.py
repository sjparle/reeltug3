from time import sleep

from PyQt5 import QtCore, QtWidgets, uic
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QTableWidgetItem, QMessageBox

from config import API_REFRESH_TIME
from queue_management import QueueManagement
from ui.resources import gui_queue_path
from ui.workers import QueueThread


class QueueWindow(QtWidgets.QMainWindow):
    update_queue_table = pyqtSignal()

    def __init__(self, mainwindow):
        super(QueueWindow, self).__init__()
        uic.loadUi(gui_queue_path, self)

        self.mainwindow = mainwindow
        self.queue_batches = self.mainwindow.queue_batches
        self.queue_connected = False

        table_render_queue_modify = self.table_queue.horizontalHeader()
        table_render_queue_modify.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        table_render_queue_modify.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        table_render_queue_modify.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        table_render_queue_modify.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        table_render_queue_modify.setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)

        self.update_queue_table.connect(self.update_queue_table_gui)
        self.button_load.clicked.connect(self.load_video)
        self.threadpool = QtCore.QThreadPool()
        self.start_queue_management_thread()

    def update_queue_table_gui(self):
        with self.mainwindow.queue_lock:
            queue_items = list(self.queue_batches)
        while self.table_queue.rowCount() > 0:
            self.table_queue.removeRow(0)
        self.table_queue.setRowCount(0)
        queue_items.sort(key=lambda item: item["time_arrived"], reverse=True)
        row = 0
        for reel in queue_items:
            if self._show_in_queue_table(reel):
                self.table_queue.insertRow(row)
                self.table_queue.setItem(row, 0, QTableWidgetItem(str(reel["time_arrived"])))
                self.table_queue.setItem(row, 1, QTableWidgetItem(str(reel["order_number"])))
                self.table_queue.setItem(row, 2, QTableWidgetItem(str(reel["item_number"])))
                self.table_queue.setItem(row, 3, QTableWidgetItem(str(reel["id"])))
                prep_state = reel.get("prep_state", "READY")
                debug_flags = (
                    f"req_rev={int(bool(reel.get('pre_reverse_required', False)))} "
                    f"pre_rev={int(bool(reel.get('pre_reversed', False)))}"
                )
                self.table_queue.setItem(row, 4, QTableWidgetItem(f"{prep_state} | {debug_flags}"))
                self.table_queue.setItem(row, 5, QTableWidgetItem(str(reel["splits"])))
                row += 1

        if self.queue_connected is False:
            self.label_orderflow_status.setText("Queue Connection: Disconnected")
        else:
            self.label_orderflow_status.setText("Queue Connection: Connected")

    def _show_in_queue_table(self, reel):
        state = str(reel.get("state", ""))
        hidden_states = {
            "TO_RENDER",
            "RENDERING",
            "TRIMMING",
            "REVERSING",
            "ADDING AUDIO",
            "FINISHING UP",
            "WAITING_FOR_CONVERTX",
            "DONE",
        }
        return state not in hidden_states

    def start_queue_management_thread(self):
        queue_thread = QueueThread(self.start_queue_refresh, self.mainwindow, self)
        self.threadpool.start(queue_thread)

    def start_queue_refresh(self, mainwindow, queue_self):
        queue_management = QueueManagement(mainwindow, queue_self)
        while not getattr(mainwindow, "stop_background_workers", False):
            queue_management.queue_handler()
            for _ in range(max(1, API_REFRESH_TIME)):
                if getattr(mainwindow, "stop_background_workers", False):
                    break
                sleep(1)

    def load_video(self):
        if self.queue_connected is False:
            self.pop_up_msg("Application is currently not recieving data from OrderFlow.", "Not connected to OrderFlow.")
            return
        try:
            row = self.table_queue.selectedItems()[0].row()
        except IndexError:
            self.pop_up_msg("No batch selected, select a batch.", "Queued batch not selected")
            return
        reel_id = int(self.table_queue.item(row, 3).text())
        with self.mainwindow.queue_lock:
            reel = [d for d in self.queue_batches if d["id"] == reel_id][0]
        if reel.get("prep_state") not in (None, "READY"):
            self.pop_up_msg(
                f"Reel is still preparing working media ({reel.get('prep_state')}). Please try again in a moment.",
                "Reel not ready",
            )
            return
        reel["state"] = "EDITING"
        self.mainwindow.load_reel(reel)
        self.update_queue_table_gui()
        self.close()

    def pop_up_msg(self, text, title):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setText(text)
        msg.setWindowTitle(title)
        msg.exec_()
