import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
GROQ_API_KEY: str = os.environ["GROQ_API_KEY"]

# Groq Whisper model
WHISPER_MODEL: str = "whisper-large-v3-turbo"

# Audio chunking settings
CHUNK_SECONDS: int = 5
LONG_AUDIO_THRESHOLD: int = 180  # seconds — switch to two-pass chunking above this
LONG_AUDIO_BLOCK_SECONDS: int = 60  # first-pass block size for long files

# Telegram flood-wait protection
MIN_EDIT_INTERVAL: float = 1.5  # minimum seconds between edit_message_text calls

# Whisper prompt for punctuation quality
WHISPER_PROMPT: str = (
    "Привет! Расставь, пожалуйста, правильные знаки препинания, "
    "заглавные буквы и оформи текст грамотно."
)

# Telegram message length limit
TG_MESSAGE_LIMIT: int = 4096
