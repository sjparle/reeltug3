import os
import shutil
import sys


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def resolve_binary(exe_name: str) -> str:
    bundled = resource_path(exe_name)
    if os.path.isfile(bundled):
        return bundled
    from_path = shutil.which(exe_name)
    if from_path:
        return from_path
    return exe_name


ffmpeg_path = resolve_binary("ffmpeg.exe")
ffprobe_path = resolve_binary("ffprobe.exe")
gui_main_path = resource_path("gui_main.ui")
gui_render_path = resource_path("gui_render.ui")
gui_queue_path = resource_path("gui_queue.ui")
gui_settings_path = resource_path("gui_settings.ui")
