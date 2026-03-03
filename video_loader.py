import os

class VideoLoader:
    def __init__(self, mainwindow):
        self.mainwindow = mainwindow
        ""
    
    def open_video(self, video_dir):
        self.media = self.mainwindow.vlc_instance.media_new(video_dir)
        self.mainwindow.mediaplayer.set_media(self.media)
        self.mainwindow.mediaplayer.play()

    def load_folder(self):
        ""
