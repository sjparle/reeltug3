import getpass
import os
import sys


def _load_env_file(path: str) -> None:
    if not path or not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    except OSError:
        # Fall back to process environment only if .env cannot be read.
        return


def _env(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value if value else default


# Load .env from common launch locations so shortcut/frozen launches still work.
_candidate_env_paths = [
    os.path.join(os.getcwd(), ".env"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    os.path.join(os.path.dirname(sys.executable), ".env"),
]
for _env_path in _candidate_env_paths:
    _load_env_file(_env_path)

USER = getpass.getuser()

TEMP_VIDEO_PROCESSING_DIR = _env("REELTUG_TEMP_VIDEO_PROCESSING_DIR", r"D:\processing")
RENDER_LOG_DIR = _env("REELTUG_RENDER_LOG_DIR", os.path.join(TEMP_VIDEO_PROCESSING_DIR, "render_logs"))
PICKLE_BACKUP_DIR = _env("REELTUG_PICKLE_BACKUP_DIR", r"D:\reeltug_backup")
MUSIC_AUDIO_DIR = _env("REELTUG_MUSIC_AUDIO_DIR", r".\cinemusic.mp3")

ORDER_FLOW_IP = _env("REELTUG_ORDER_FLOW_IP", "http://10.0.0.54:8547")
API_REFRESH_TIME = int(_env("REELTUG_API_REFRESH_TIME", "60"))
TRANSFERRING_DIRECTORY = _env("REELTUG_TRANSFERRING_DIRECTORY", r"\\Tapebox-2\Tapebox2\7 - Transferring")
CINE_EDITING_DIR = _env("REELTUG_CINE_EDITING_DIR", r"\\TapeBox\TapeBox\6 - Cine Editing")

C2D_OUT_DIR = _env("REELTUG_C2D_OUT_DIR", r"C:\ConvertXToDVD")
C2D_EXE_DIR = _env("REELTUG_C2D_EXE_DIR", r"C:\Program Files (x86)\VSO\ConvertX\7\ConvertXtoDvd.exe")
C2D_MENU_DIR = _env(
    "REELTUG_C2D_MENU_DIR",
    fr"C:\Users\{USER}\Documents\ConvertXtoDVD_Resources\Templates\DC_Cine_DVD_v2\DC_Cine_DVD_v2.ini",
)

# API auth/config, now env-driven to avoid hardcoded credentials in source.
API_HOST = _env("REELTUG_API_HOST", "http://10.0.0.49:9797/api")
API_USERNAME = _env("REELTUG_API_USERNAME", "")
API_PASSWORD = _env("REELTUG_API_PASSWORD", "")
API_TIMEOUT_SECONDS = int(_env("REELTUG_API_TIMEOUT_SECONDS", "20"))
QUEUE_FETCH_TIMEOUT_SECONDS = int(_env("REELTUG_QUEUE_FETCH_TIMEOUT_SECONDS", "120"))

if not os.path.isdir(TRANSFERRING_DIRECTORY):
    print("TRANSFERRING_DIRECTORY is not available")
