# VoiceToText

Telegram-бот на Python 3.11 / aiogram 3.13.1:
- голосовые сообщения расшифровываются через прежний посегментный генератор;
- видеокружки получают встроенные синхронизированные ASS-субтитры и возвращаются через `sendVideoNote`.

## Как работают кружки

1. Бот скачивает MP4 во временную уникальную директорию.
2. FFprobe проверяет видео, длительность и наличие звука.
3. FFmpeg извлекает моно OGG/Opus 16 кГц.
4. Groq Whisper получает `response_format=verbose_json` и `timestamp_granularities[]=segment`.
5. Только реальные `segments.start/end` используются в ASS. При отсутствии сегментов текст не растягивается по длительности видео: выдаётся ошибка конфигурации/API.
6. FFmpeg/libass встраивает белые субтитры с тёмной полупрозрачной подложкой внизу кадра. Кириллица поддерживается шрифтом DejaVu Sans. Аудио сохраняется в AAC.
7. Результат отправляется именно `sendVideoNote`. Если Telegram отклоняет кружок, бот явно сообщает об этом и отправляет обычное видео.

Штатный квадратный кадр не обрезается и не растягивается. Для нестандартного неквадратного источника используется размытый фон, а весь исходный кадр сохраняется. FFmpeg запускается через `asyncio.to_thread`, не блокируя event loop. Тяжёлые обработки ограничены семафором. Временная директория удаляется после успеха или ошибки.

Если звука/речи нет, возвращается исходный кружок без пустых субтитров.

## Локальный запуск

```bash
python3.11 -m venv .venv
. .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # укажите BOT_TOKEN и GROQ_API_KEY
# Debian/Ubuntu:
sudo apt-get update && sudo apt-get install ffmpeg fonts-dejavu-core
python main.py
```

Проверьте кодировщики/фильтры:

```bash
ffmpeg -hide_banner -encoders | grep libx264
ffmpeg -hide_banner -filters | grep subtitles
```

## Docker / Render

```bash
docker build -t voice-to-text .
docker run --rm --env-file .env -p 8080:8080 voice-to-text
```

В Dockerfile уже установлены FFmpeg и DejaVu Sans. На Render задайте переменные окружения из `.env.example`; `PORT` обычно выставляет Render. Сервис использует polling и HTTP healthcheck `GET /` для UptimeRobot. Не включайте webhook одновременно с polling.

Перед запуском убедитесь, что выбранная модель `whisper-large-v3-turbo` в вашем аккаунте Groq поддерживает `verbose_json` и сегментные тайминги. Ответ без `segments` намеренно считается ошибкой, а не поводом выдумывать синхронизацию.

## Переменные окружения

Обязательные: `BOT_TOKEN`, `GROQ_API_KEY`.

Необязательные (значения по умолчанию):

| Переменная | По умолчанию |
|---|---:|
| `PORT` | `8080` |
| `MAX_CONCURRENT_VIDEOS` | `2` |
| `MAX_VIDEO_SECONDS` | `60` |
| `MAX_VIDEO_FILE_MB` | `20` |
| `GROQ_TIMEOUT_SECONDS` | `90` |
| `GROQ_RETRIES` | `3` |
| `FFMPEG_TIMEOUT_SECONDS` | `180` |
| `VIDEO_CRF` | `20` |
| `VIDEO_PRESET` | `medium` |
| `SUBTITLE_FONT` | `DejaVu Sans` |
| `SUBTITLE_FONT_SCALE` | `0.055` |
| `SUBTITLE_MAX_CHARS_PER_LINE` | `28` |

Для слабого Render можно использовать `MAX_CONCURRENT_VIDEOS=1` и `VIDEO_PRESET=fast`.

## Тесты и необходимая интеграционная проверка

```bash
python -m unittest discover -s tests -v
```

Тесты не требуют сетевых ключей и проверяют кириллицу, перенос строк, паузы, границы ASS и валидацию ответа Groq.

Перед production необходимо отдельно провести реальные проверки с Telegram/Groq/FFmpeg: короткий и длинный кружок, речь с паузами, длинная фраза, отсутствие речи, сохранение звука, два одновременных запроса. Юнит-тесты не заменяют эти проверки.
