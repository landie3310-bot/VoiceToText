import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
GROQ_API_KEY: str = os.environ["GROQ_API_KEY"]

# Полная v3 точнее turbo, а длинные фрагменты сокращают число API-запросов.
WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "whisper-large-v3")

# Длинные фрагменты не разрывают слова каждые пять секунд и дают модели контекст.
CHUNK_SECONDS: int = max(5, int(os.getenv("CHUNK_SECONDS", "30")))
LONG_AUDIO_THRESHOLD: int = 180
LONG_AUDIO_BLOCK_SECONDS: int = 60

# Telegram flood-wait protection
MIN_EDIT_INTERVAL: float = 1.5

WHISPER_PROMPT: str = (
    "Это естественная русская устная речь. Точно передавай все слова, имена и числа. "
    "Используй грамотную русскую пунктуацию: запятые, тире, вопросительные и "
    "восклицательные знаки. Начинай предложения с заглавной буквы и завершай их точкой. "
    "Не добавляй пояснений и не изменяй смысл. Например: Привет, как дела? "
    "Я сегодня задержусь, поэтому давай встретимся завтра."
)

TG_MESSAGE_LIMIT: int = 4096

# Video-note pipeline. Telegram video notes are limited to 60 seconds.
MAX_VIDEO_SECONDS: int = min(60, max(1, int(os.getenv("MAX_VIDEO_SECONDS", "60"))))
MAX_VIDEO_FILE_MB: int = min(20, max(1, int(os.getenv("MAX_VIDEO_FILE_MB", "20"))))
MAX_CONCURRENT_VIDEOS: int = max(1, int(os.getenv("MAX_CONCURRENT_VIDEOS", "2")))
GROQ_TIMEOUT_SECONDS: float = float(os.getenv("GROQ_TIMEOUT_SECONDS", "90"))
GROQ_RETRIES: int = max(1, int(os.getenv("GROQ_RETRIES", "3")))
FFMPEG_TIMEOUT_SECONDS: int = int(os.getenv("FFMPEG_TIMEOUT_SECONDS", "180"))
VIDEO_CRF: int = int(os.getenv("VIDEO_CRF", "23"))
VIDEO_PRESET: str = os.getenv("VIDEO_PRESET", "ultrafast")
VIDEO_THREADS: int = max(1, int(os.getenv("VIDEO_THREADS", "2")))
SUBTITLE_FONT: str = os.getenv("SUBTITLE_FONT", "DejaVu Sans")
SUBTITLE_FONT_SCALE: float = float(os.getenv("SUBTITLE_FONT_SCALE", "0.046"))
SUBTITLE_MAX_CHARS_PER_LINE: int = max(
    12, int(os.getenv("SUBTITLE_MAX_CHARS_PER_LINE", "20"))
)
# Word timestamps поддерживаются полной v3; turbo остаётся у голосовых сообщений.
VIDEO_WHISPER_MODEL: str = os.getenv("VIDEO_WHISPER_MODEL", "whisper-large-v3")
