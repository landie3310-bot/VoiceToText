"""
main.py — точка входа Telegram-бота.

Поддерживает:
- /start
- voice (голосовые сообщения)
- video_note (видеокружки)
- пересланные сообщения с этими типами медиа
- защиту от FloodWait (TelegramRetryAfter + минимальный интервал редактирования)
- разбивку текста на два сообщения, если превышен лимит 4096 символов
- aiohttp веб-сервер для пинга UptimeRobot (Render Web Service)
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web

import config
import transcriber

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = Router()

# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Система готова к работе. "
        "Отправьте или перешлите голосовое сообщение либо видеосообщение для расшифровки."
    )


# ---------------------------------------------------------------------------
# Обработчик голосовых и видеосообщений
# ---------------------------------------------------------------------------

@router.message(F.voice | F.video_note)
async def handle_media(message: Message, bot: Bot) -> None:
    await _process_media(message, bot)


# Пересланные сообщения с нужным медиа
@router.message(F.forward_from | F.forward_from_chat | F.forward_date)
async def handle_forwarded(message: Message, bot: Bot) -> None:
    if message.voice or message.video_note:
        await _process_media(message, bot)


# ---------------------------------------------------------------------------
# Ответ на всё остальное
# ---------------------------------------------------------------------------

@router.message()
async def handle_unsupported(message: Message) -> None:
    if message.voice or message.video_note:
        # Попало сюда по какой-то причине — на всякий случай
        await _process_media(message, bot=message.bot)
        return
    await message.answer(
        "Неподдерживаемый формат. Отправьте голосовое или видеосообщение."
    )


# ---------------------------------------------------------------------------
# Основная логика обработки
# ---------------------------------------------------------------------------

async def _process_media(message: Message, bot: Bot) -> None:
    """
    Скачивает медиафайл, запускает транскрипцию через генератор transcriber,
    постепенно редактирует сообщение-placeholder.
    """
    # Определяем тип и file_id
    is_video = False
    if message.voice:
        file_id = message.voice.file_id
    elif message.video_note:
        file_id = message.video_note.file_id
        is_video = True
    else:
        await message.answer(
            "Неподдерживаемый формат. Отправьте голосовое или видеосообщение."
        )
        return

    # Отправляем placeholder
    status_msg = await message.answer("Обработка аудио...")

    # Временный файл для скачивания
    suffix = ".mp4" if is_video else ".ogg"
    tmp_path = os.path.join(tempfile.gettempdir(), f"tgbot_{uuid.uuid4().hex}{suffix}")

    try:
        # --- Скачать файл ---
        file = await bot.get_file(file_id)
        await bot.download_file(file.file_path, destination=tmp_path)

        # --- Транскрипция через генератор ---
        full_text = ""
        current_msg = status_msg
        last_edit_time = 0.0
        edit_buffer = ""       # буфер накопленных кусков между редактированиями
        first_piece = True

        async for new_piece in transcriber.transcribe_audio(tmp_path, is_video=is_video):
            if not new_piece:
                continue

            edit_buffer_text = (full_text + (" " if full_text else "") + new_piece).strip()
            # Обновляем полный текст
            full_text = edit_buffer_text

            now = asyncio.get_event_loop().time()
            elapsed = now - last_edit_time

            # Если прошло недостаточно времени — делаем задержку (flood-wait guard)
            wait_needed = config.MIN_EDIT_INTERVAL - elapsed
            if wait_needed > 0:
                await asyncio.sleep(wait_needed)

            # Редактируем или отправляем сообщение
            current_msg = await _safe_edit_or_send(
                bot=bot,
                chat_id=message.chat.id,
                current_msg=current_msg,
                text=full_text,
                first_piece=first_piece,
            )
            first_piece = False
            last_edit_time = asyncio.get_event_loop().time()

        # Финальное обновление, если текст остался (на случай, если генератор
        # завершился без последнего редактирования из-за ожидания)
        if not full_text:
            await current_msg.edit_text("Речь не обнаружена.")

    except Exception as exc:
        logger.exception("Ошибка обработки: %s", exc)
        try:
            await status_msg.edit_text(
                "Ошибка обработки файла. Повторите попытку позже."
            )
        except Exception:
            pass
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


async def _safe_edit_or_send(
    bot: Bot,
    chat_id: int,
    current_msg: Message,
    text: str,
    first_piece: bool,
) -> Message:
    """
    Редактирует текущее сообщение. Если текст превышает лимит — отправляет
    первую часть в текущем сообщении, вторую — новым.
    Перехватывает TelegramRetryAfter.
    """
    LIMIT = config.TG_MESSAGE_LIMIT

    if len(text) <= LIMIT:
        try:
            await current_msg.edit_text(text)
        except TelegramRetryAfter as e:
            logger.warning("FloodWait %s сек., ожидаю...", e.retry_after)
            await asyncio.sleep(e.retry_after + 0.5)
            await current_msg.edit_text(text)
        except Exception as e:
            # Если сообщение не изменилось — Telegram бросит ошибку; игнорируем
            if "message is not modified" not in str(e).lower():
                logger.warning("edit_text ошибка: %s", e)
        return current_msg
    else:
        # Разбиваем на две части по границе слова
        split_at = _find_split_point(text, LIMIT)
        part1 = text[:split_at].rstrip()
        part2 = text[split_at:].lstrip()

        try:
            await current_msg.edit_text(part1)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
            await current_msg.edit_text(part1)
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                logger.warning("edit_text (часть 1) ошибка: %s", e)

        # Отправляем вторую часть новым сообщением
        try:
            new_msg = await bot.send_message(chat_id=chat_id, text=part2)
            return new_msg
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
            new_msg = await bot.send_message(chat_id=chat_id, text=part2)
            return new_msg


def _find_split_point(text: str, limit: int) -> int:
    """Находит позицию разбивки не позже limit символов, по границе слова/предложения."""
    # Предпочтительно — по концу предложения
    for sep in (". ", "! ", "? ", "\n"):
        idx = text.rfind(sep, 0, limit)
        if idx != -1:
            return idx + len(sep)
    # Крайний случай — по пробелу
    idx = text.rfind(" ", 0, limit)
    if idx != -1:
        return idx + 1
    return limit


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

async def _healthcheck(request: web.Request) -> web.Response:
    """GET / и HEAD / — для пингера UptimeRobot."""
    return web.Response(text="Alive")


async def _run_web_server() -> None:
    """Запускает лёгкий aiohttp-сервер на порту PORT (по умолчанию 8080)."""
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_route("GET", "/", _healthcheck)
    app.router.add_route("HEAD", "/", _healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Веб-сервер запущен на порту %d.", port)


async def main() -> None:
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    # Запускаем веб-сервер и поллинг параллельно в одном event loop
    await _run_web_server()

    logger.info("Бот запущен.")
    await dp.start_polling(bot, allowed_updates=["message"])


if __name__ == "__main__":
    asyncio.run(main())
