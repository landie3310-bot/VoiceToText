import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
GROQ_API_KEY: str = os.environ["GROQ_API_KEY"]

# Groq Whisper model
WHISPER_MODEL: str = "whisper-large-v3-turbo"

# Audio chunking settings (the original voice-message pipeline)
CHUNK_SECONDS: int = 5
LONG_AUDIO_THRESHOLD: int = 180
LONG_AUDIO_BLOCK_SECONDS: int = 60

# Telegram flood-wait protection
MIN_EDIT_INTERVAL: float = 1.5

WHISPER_PROMPT: str = (
    "Привет! Расставь, пожалуйста, правильные знаки препинания, "
    "заглавные буквы и оформи текст грамотно."
)

TG_MESSAGE_LIMIT: int = 4096

# Video-note pipeline. Telegram video notes are limited to 60 seconds.
MAX_VIDEO_SECONDS: int = min(60, max(1, int(os.getenv("MAX_VIDEO_SECONDS", "60"))))
MAX_VIDEO_FILE_MB: int = min(20, max(1, int(os.getenv("MAX_VIDEO_FILE_MB", "20"))))
MAX_CONCURRENT_VIDEOS: int = max(1, int(os.getenv("MAX_CONCURRENT_VIDEOS", "2")))
GROQ_TIMEOUT_SECONDS: float = float(os.getenv("GROQ_TIMEOUT_SECONDS", "90"))
GROQ_RETRIES: int = max(1, int(os.getenv("GROQ_RETRIES", "3")))
FFMPEG_TIMEOUT_SECONDS: int = int(os.getenv("FFMPEG_TIMEOUT_SECONDS", "180"))
VIDEO_CRF: int = int(os.getenv("VIDEO_CRF", "20"))
VIDEO_PRESET: str = os.getenv("VIDEO_PRESET", "medium")
SUBTITLE_FONT: str = os.getenv("SUBTITLE_FONT", "DejaVu Sans")
SUBTITLE_FONT_SCALE: float = float(os.getenv("SUBTITLE_FONT_SCALE", "0.055"))
SUBTITLE_MAX_CHARS_PER_LINE: int = max(
    16, int(os.getenv("SUBTITLE_MAX_CHARS_PER_LINE", "28"))
)
