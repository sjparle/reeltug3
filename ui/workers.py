from PyQt5 import QtCore
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot


class workerSignals(QObject):
    update_reel_table = pyqtSignal(int, int, str)
    update_render_table = pyqtSignal()
    update_queue_table = pyqtSignal()
    signal_preview_loading = pyqtSignal(float)


class RenderThread(QtCore.QRunnable):
    def __init__(self, fn, arg1, arg2, arg3):
        super(RenderThread, self).__init__()
        self.fn = fn
        self.arg1 = arg1
        self.arg2 = arg2
        self.arg3 = arg3
        self.signals = workerSignals()

    @pyqtSlot()
    def run(self):
        self.fn(self.arg1, self.arg2, self.arg3)


class QueueThread(QtCore.QRunnable):
    def __init__(self, fn, arg1, arg2):
        super(QueueThread, self).__init__()
        self.fn = fn
        self.arg1 = arg1
        self.arg2 = arg2
        self.signals = workerSignals()

    @pyqtSlot()
    def run(self):
        self.fn(self.arg1, self.arg2)


class PreviewThread(QtCore.QRunnable):
    def __init__(self, fn, arg1, arg2, arg3, arg4):
        super(PreviewThread, self).__init__()
        self.fn = fn
        self.arg1 = arg1
        self.arg2 = arg2
        self.arg3 = arg3
        self.arg4 = arg4
        self.signals = workerSignals()

    @pyqtSlot()
    def run(self):
        self.fn(self.arg1, self.arg2, self.arg3, self.arg4)
