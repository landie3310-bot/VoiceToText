"""Telegram-бот: прежняя расшифровка голосовых и кружки с субтитрами."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramRetryAfter,
)
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message
from aiohttp import web

import config
import transcriber
from video_note import VideoProcessingError, process_video_note

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
router = Router()
video_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_VIDEOS)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Система готова к работе. Отправьте голосовое сообщение для расшифровки "
        "или видеокружок — бот добавит субтитры прямо в видео."
    )


@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot) -> None:
    await _process_voice(message, bot)


@router.message(F.video_note)
async def handle_video_note(message: Message, bot: Bot) -> None:
    await _process_video_note(message, bot)


@router.message(F.forward_from | F.forward_from_chat | F.forward_date)
async def handle_forwarded(message: Message, bot: Bot) -> None:
    if message.voice:
        await _process_voice(message, bot)
    elif message.video_note:
        await _process_video_note(message, bot)


@router.message()
async def handle_unsupported(message: Message) -> None:
    await message.answer(
        "Неподдерживаемый формат. Отправьте голосовое сообщение или видеокружок."
    )


async def _process_voice(message: Message, bot: Bot) -> None:
    """Исходный сценарий: генератор текста и постепенное редактирование."""
    if not message.voice:
        return
    status_msg = await message.answer("Обработка аудио...")
    tmp_path = os.path.join(tempfile.gettempdir(), f"tgbot_{uuid.uuid4().hex}.ogg")
    try:
        file = await bot.get_file(message.voice.file_id)
        await bot.download_file(file.file_path, destination=tmp_path)
        full_text = ""
        current_msg = status_msg
        last_edit_time = 0.0
        first_piece = True

        async for new_piece in transcriber.transcribe_audio(tmp_path, is_video=False):
            if not new_piece:
                continue
            full_text = (full_text + (" " if full_text else "") + new_piece).strip()
            wait_needed = config.MIN_EDIT_INTERVAL - (
                asyncio.get_running_loop().time() - last_edit_time
            )
            if wait_needed > 0:
                await asyncio.sleep(wait_needed)
            current_msg = await _safe_edit_or_send(
                bot=bot,
                chat_id=message.chat.id,
                current_msg=current_msg,
                text=full_text,
                first_piece=first_piece,
            )
            first_piece = False
            last_edit_time = asyncio.get_running_loop().time()
        if not full_text:
            await current_msg.edit_text("Речь не обнаружена.")
    except Exception as exc:
        logger.exception("Ошибка обработки голосового: %s", exc)
        try:
            await status_msg.edit_text("Ошибка обработки файла. Повторите попытку позже.")
        except Exception:
            logger.exception("Не удалось обновить статус голосового сообщения")
    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("Не удалось удалить %s", tmp_path)


async def _process_video_note(message: Message, bot: Bot) -> None:
    """Независимый сценарий: реальные тайминги, ASS, FFmpeg, sendVideoNote."""
    note = message.video_note
    if not note:
        return
    if note.file_size and note.file_size > config.MAX_VIDEO_FILE_MB * 1024 * 1024:
        await message.answer("Видео слишком большое для обработки Telegram-ботом.")
        return
    if note.duration > config.MAX_VIDEO_SECONDS:
        await message.answer("Видеокружок должен длиться не более 60 секунд.")
        return

    status = await message.answer(
        "Обрабатываю видеокружок, распознаю речь и добавляю субтитры…"
    )
    try:
        async with video_semaphore:
            with tempfile.TemporaryDirectory(prefix=f"video_note_{uuid.uuid4().hex}_") as tmp:
                workdir = Path(tmp)
                source = workdir / "source.mp4"
                file = await bot.get_file(note.file_id)
                if not file.file_path:
                    raise VideoProcessingError("Telegram не вернул путь к файлу.")
                await bot.download_file(file.file_path, destination=source)
                result, info, original_without_speech = await process_video_note(source, workdir)

                if original_without_speech is not None:
                    await status.edit_text("Речь не обнаружена. Возвращаю исходный кружок.")
                try:
                    await bot.send_video_note(
                        chat_id=message.chat.id,
                        video_note=FSInputFile(result),
                        duration=max(1, round(info.duration)),
                        length=min(info.width, info.height),
                    )
                except (TelegramBadRequest, TelegramEntityTooLarge) as exc:
                    logger.warning("sendVideoNote не принят Telegram: %s", exc)
                    await status.edit_text(
                        "Telegram не принял результат как кружок. "
                        "Отправляю его отдельно обычным видео."
                    )
                    await bot.send_video(
                        chat_id=message.chat.id,
                        video=FSInputFile(result),
                        supports_streaming=True,
                    )
                    return
                if original_without_speech is None:
                    await status.edit_text("Готово — субтитры встроены в видеокружок.")
    except Exception as exc:
        logger.exception("Ошибка обработки видеокружка: %s", exc)
        try:
            await status.edit_text(
                "Не удалось обработать видеокружок. Попробуйте ещё раз позже."
            )
        except Exception:
            logger.exception("Не удалось отправить статус ошибки")


async def _safe_edit_or_send(
    bot: Bot,
    chat_id: int,
    current_msg: Message,
    text: str,
    first_piece: bool,
) -> Message:
    """Прежняя защита голосовой расшифровки от лимитов Telegram."""
    limit = config.TG_MESSAGE_LIMIT
    if len(text) <= limit:
        try:
            await current_msg.edit_text(text)
        except TelegramRetryAfter as exc:
            logger.warning("FloodWait %s сек., ожидаю...", exc.retry_after)
            await asyncio.sleep(exc.retry_after + 0.5)
            await current_msg.edit_text(text)
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                logger.warning("edit_text ошибка: %s", exc)
        return current_msg

    split_at = _find_split_point(text, limit)
    part1 = text[:split_at].rstrip()
    part2 = text[split_at:].lstrip()
    try:
        await current_msg.edit_text(part1)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after + 0.5)
        await current_msg.edit_text(part1)
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            logger.warning("edit_text (часть 1) ошибка: %s", exc)
    try:
        return await bot.send_message(chat_id=chat_id, text=part2)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after + 0.5)
        return await bot.send_message(chat_id=chat_id, text=part2)


def _find_split_point(text: str, limit: int) -> int:
    for separator in (". ", "! ", "? ", "\n"):
        index = text.rfind(separator, 0, limit)
        if index != -1:
            return index + len(separator)
    index = text.rfind(" ", 0, limit)
    return index + 1 if index != -1 else limit


async def _healthcheck(request: web.Request) -> web.Response:
    return web.Response(text="Alive")


async def _run_web_server() -> None:
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
    await _run_web_server()
    logger.info("Бот запущен.")
    try:
        await dp.start_polling(bot, allowed_updates=["message"])
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
