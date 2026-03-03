from PyQt5.QtWidgets import QLabel
from PyQt5.QtCore import pyqtSignal, QObject, pyqtSlot



class LabelPreviewStart(QLabel):
    label_signal_start = pyqtSignal(str)

    def __init__(self, parent=None):
        self.highlighted = False
        # self.signals = PreviewChanged()
        super(QLabel, self).__init__(parent)
    
    # @pyqtSlot()
    def mousePressEvent(self, event):
        alpha = "0" if self.highlighted else "3"
        self.setStyleSheet(f"border: {alpha}px solid blue;")
        self.highlighted = not self.highlighted
        self.label_signal_start.emit(str(id(self)))
    #     self.signals.preivew_changed.emit()
    #     self.signals.preivew_changed.connect(self.remove_highlight)
        
    # def remove_highlight(self):
    #     print("running this")
    #     self.setStyleSheet(f"border: 0px solid blue;")