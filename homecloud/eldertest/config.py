#
# # 建議放環境變數，不要硬寫在主程式
#
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent
DOTENV_PATH = BASE_DIR.parent / ".env"
if load_dotenv is not None and DOTENV_PATH.exists():
    # The key changed from the local settings page must take precedence over
    # an older user/machine environment variable left by a previous demo.
    load_dotenv(DOTENV_PATH, override=True)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# ===== 基本服務 =====
RTSP_INPUT_URL = _env("RTSP_INPUT_URL", "rtsp://10.121.187.243:8554/mystream")
RTSP_OUTPUT_URL = _env("RTSP_OUTPUT_URL", "rtsp://10.121.187.243:8554/result")
ROBOT_CONTROL_URL = _env("ROBOT_CONTROL_URL", "http://10.121.187.243:5001/control")
WEB_UI_HOST = _env("WEB_UI_HOST", "0.0.0.0")
WEB_UI_PORT = int(_env("WEB_UI_PORT", "5000"))
WEB_UI_CORS_ORIGINS = [x.strip() for x in _env("WEB_UI_CORS_ORIGINS", "*").split(",") if x.strip()]

# ===== 模型 =====
YOLO_MODEL_PATH = _env("YOLO_MODEL_PATH", "models/best.pt")
DEPTH_MODEL_PATH = _env("DEPTH_MODEL_PATH", "models/depth_anything_v2_vits.pth")

# ===== 外部服務 =====
LINE_CHANNEL_ACCESS_TOKEN = _env("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = _env("LINE_CHANNEL_SECRET", "")
LINE_USER_ID = _env("LINE_USER_ID", "")
GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
GEMINI_MODEL_NAME = _env("GEMINI_MODEL_NAME", "gemini-flash-lite-latest")
ROBOT_AUDIO_URL = _env("ROBOT_AUDIO_URL", "http://10.129.211.235:5002")
ROBOT_AUDIO_TOKEN = _env("ROBOT_AUDIO_TOKEN", "")
CARE_WEB_HOST = _env("CARE_WEB_HOST", "0.0.0.0")

# ===== 主模式 =====
DEFAULT_SYSTEM_MODE = _env("DEFAULT_SYSTEM_MODE", "CARE")  # CARE / BALL / FOLLOW / MANUAL
PERSON_CLASS_ID = int(_env("PERSON_CLASS_ID", "1"))
BALL_CLASS_ID = int(_env("BALL_CLASS_ID", "0"))
PROCESS_FPS = int(_env("PROCESS_FPS", "12"))
JPEG_QUALITY = int(_env("JPEG_QUALITY", "75"))
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()

# ===== 跟隨/撿球參數 =====
FOLLOW_TOLERANCE_X = int(_env("FOLLOW_TOLERANCE_X", "90"))
FOLLOW_TOLERANCE_Y = int(_env("FOLLOW_TOLERANCE_Y", "60"))
FOLLOW_TARGET_DEPTH_MIN = int(_env("FOLLOW_TARGET_DEPTH_MIN", "80"))
FOLLOW_TARGET_DEPTH_MAX = int(_env("FOLLOW_TARGET_DEPTH_MAX", "170"))
FOLLOW_SAFE_DEPTH = int(_env("FOLLOW_SAFE_DEPTH", "120"))
CENTER_LEFT_RATIO = float(_env("CENTER_LEFT_RATIO", "0.42"))
CENTER_RIGHT_RATIO = float(_env("CENTER_RIGHT_RATIO", "0.58"))
BALL_PICK_DEPTH = int(_env("BALL_PICK_DEPTH", "180"))
BALL_FORWARD_DEPTH = int(_env("BALL_FORWARD_DEPTH", "80"))

# ===== 避障 =====
OBSTACLE_DETECTION_ENABLED = _env("OBSTACLE_DETECTION_ENABLED", "1") == "1"
OBSTACLE_WARNING_DEPTH = int(_env("OBSTACLE_WARNING_DEPTH", "70"))
OBSTACLE_STOP_DEPTH = int(_env("OBSTACLE_STOP_DEPTH", "45"))
ULTRASONIC_ENABLED = _env("ULTRASONIC_ENABLED", "0") == "1"
ULTRASONIC_API_URL = _env("ULTRASONIC_API_URL", "http://127.0.0.1:5001/ultrasonic")
ULTRASONIC_STOP_CM = float(_env("ULTRASONIC_STOP_CM", "25"))

# ===== 長照監測 =====
FALL_DETECTION_ENABLED = _env("FALL_DETECTION_ENABLED", "1") == "1"
FALL_ANGLE_THRESHOLD = float(_env("FALL_ANGLE_THRESHOLD", "45"))
FALL_DURATION_THRESHOLD = float(_env("FALL_DURATION_THRESHOLD", "3.0"))
IMMOBILE_DURATION_THRESHOLD = float(_env("IMMOBILE_DURATION_THRESHOLD", "30.0"))
HEALTH_REMINDER_ENABLED = _env("HEALTH_REMINDER_ENABLED", "1") == "1"
MEDICATION_TIMES = [t.strip() for t in _env("MEDICATION_TIMES", "08:00,12:00,20:00").split(",") if t.strip()]
WATER_REMINDER_MINUTES = int(_env("WATER_REMINDER_MINUTES", "120"))
REMINDER_COOLDOWN_SEC = int(_env("REMINDER_COOLDOWN_SEC", "300"))

# ===== 語音 =====
VOICE_CONTROL_ENABLED = _env("VOICE_CONTROL_ENABLED", "1") == "1"
CARE_LOCAL_TTS_ENABLED = _env("CARE_LOCAL_TTS_ENABLED", "0") == "1"
SOUND_DETECTION_ENABLED = _env("SOUND_DETECTION_ENABLED", "1") == "1"
AUDIO_SAMPLE_RATE = int(_env("AUDIO_SAMPLE_RATE", "16000"))
AUDIO_BLOCK_SIZE = int(_env("AUDIO_BLOCK_SIZE", "8000"))
AUDIO_INPUT_DEVICE = _env("AUDIO_INPUT_DEVICE", "")
VOSK_MODEL_PATH = _env("VOSK_MODEL_PATH", "/home/pi/vosk-model-small-cn-0.22")
VOICE_COMMAND_COOLDOWN_SEC = float(_env("VOICE_COMMAND_COOLDOWN_SEC", "1.5"))
SOUND_RMS_THRESHOLD = float(_env("SOUND_RMS_THRESHOLD", "0.18"))
SCREAM_RMS_THRESHOLD = float(_env("SCREAM_RMS_THRESHOLD", "0.32"))
SOUND_ALERT_COOLDOWN_SEC = float(_env("SOUND_ALERT_COOLDOWN_SEC", "20"))

# ===== 緊急按鈕 / 觸控感測器 =====
EMERGENCY_BUTTON_ENABLED = _env("EMERGENCY_BUTTON_ENABLED", "1") == "1"
EMERGENCY_BUTTON_PIN = int(_env("EMERGENCY_BUTTON_PIN", "17"))
EMERGENCY_BUTTON_HOLD_SEC = float(_env("EMERGENCY_BUTTON_HOLD_SEC", "0.2"))
EMERGENCY_BUTTON_COOLDOWN_SEC = float(_env("EMERGENCY_BUTTON_COOLDOWN_SEC", "15"))
EMERGENCY_BUTTON_MODE = _env("EMERGENCY_BUTTON_MODE", "touch")  # touch / button
EMERGENCY_BUTTON_ACTIVE_HIGH = _env("EMERGENCY_BUTTON_ACTIVE_HIGH", "1") == "1"
EMERGENCY_BUTTON_USE_PULL_UP = _env("EMERGENCY_BUTTON_USE_PULL_UP", "0") == "1"
EMERGENCY_BUTTON_BOUNCE_SEC = float(_env("EMERGENCY_BUTTON_BOUNCE_SEC", "0.05"))
