"""
transcriber.py — аудио-конвертация и транскрипция через Groq Whisper API.

Архитектура:
- split_audio()    : нарезка аудиофайла на чанки через ffmpeg (subprocess, без pydub в памяти)
- get_duration()   : получение длительности аудио через ffprobe
- transcribe_chunk(): отправка одного чанка в Groq Whisper API
- transcribe_audio(): генератор — отправляет тексты чанков по мере готовности
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Optional

import httpx

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Утилиты ffmpeg / ffprobe
# ---------------------------------------------------------------------------

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Запускает внешнюю команду. Бросает RuntimeError при ненулевом коде."""
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Команда {cmd[0]} завершилась с кодом {result.returncode}.\n"
            f"stderr: {result.stderr.decode(errors='replace')}"
        )
    return result


def get_duration(path: str) -> float:
    """Возвращает длительность аудиофайла в секундах через ffprobe."""
    result = _run([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ])
    return float(result.stdout.decode().strip())


def extract_audio(input_path: str, output_path: str) -> None:
    """Конвертирует/извлекает аудио в OGG/OPUS (оптимально для Whisper)."""
    _run([
        "ffmpeg", "-y",
        "-i", input_path,
        "-vn",                   # убрать видеодорожку
        "-ar", "16000",          # 16 kHz — оптимум для Whisper
        "-ac", "1",              # моно
        "-c:a", "libopus",
        "-b:a", "32k",
        output_path,
    ])


def cut_segment(input_path: str, output_path: str, start: float, duration: float) -> None:
    """Вырезает сегмент [start, start+duration] из аудиофайла."""
    _run([
        "ffmpeg", "-y",
        "-ss", str(start),
        "-t", str(duration),
        "-i", input_path,
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "libopus",
        "-b:a", "32k",
        output_path,
    ])


# ---------------------------------------------------------------------------
# Groq Whisper API
# ---------------------------------------------------------------------------

async def transcribe_chunk(
    client: httpx.AsyncClient,
    chunk_path: str,
    previous_text: str = "",
) -> str:
    """
    Отправляет аудиочанк в Groq Whisper API.
    previous_text используется как "prompt" для контекста и пунктуации.
    Возвращает распознанный текст (str). Пустая строка при тишине.
    """
    prompt = config.WHISPER_PROMPT
    if previous_text:
        # Передаём последние ~224 токена предыдущего текста как контекст
        context_snippet = previous_text[-500:]
        prompt = context_snippet

    with open(chunk_path, "rb") as f:
        audio_bytes = f.read()

    filename = Path(chunk_path).name

    response = await client.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
        files={
            "file": (filename, audio_bytes, "audio/ogg"),
        },
        data={
            "model": config.WHISPER_MODEL,
            "language": "ru",
            "prompt": prompt,
            "response_format": "text",
        },
        timeout=60.0,
    )
    response.raise_for_status()

    text: str = response.text.strip()
    return text


# ---------------------------------------------------------------------------
# Публичный генератор транскрипции
# ---------------------------------------------------------------------------

async def transcribe_audio(
    source_path: str,
    is_video: bool = False,
) -> AsyncIterator[str]:
    """
    Асинхронный генератор, который:
    1. Извлекает аудио из source_path (если видео — снимает видеодорожку).
    2. Нарезает аудио на 5-секундные чанки.
       Для файлов длиннее 3 минут — сначала минутные блоки, потом 5-сек чанки.
    3. Для каждого чанка вызывает Groq API и yield-ит распознанный текст.

    Временные файлы всегда удаляются в блоке finally.
    """
    tmpdir = tempfile.mkdtemp(prefix="tgbot_")
    created_files: list[str] = []

    async with httpx.AsyncClient() as client:
        try:
            # --- Шаг 1: извлечь/конвертировать аудио ---
            audio_path = os.path.join(tmpdir, f"{uuid.uuid4().hex}_audio.ogg")
            created_files.append(audio_path)

            await asyncio.to_thread(extract_audio, source_path, audio_path)

            duration = await asyncio.to_thread(get_duration, audio_path)
            logger.info("Длительность аудио: %.1f сек", duration)

            chunk_sec = config.CHUNK_SECONDS
            block_sec = config.LONG_AUDIO_BLOCK_SECONDS
            long_threshold = config.LONG_AUDIO_THRESHOLD

            accumulated_text = ""

            if duration <= long_threshold:
                # --- Короткий файл: нарезаем сразу по 5 сек ---
                async for piece in _yield_chunks(
                    client, audio_path, 0.0, duration, chunk_sec,
                    tmpdir, created_files, accumulated_text
                ):
                    accumulated_text = piece["full"]
                    yield piece["new"]
            else:
                # --- Длинный файл: сначала минутные блоки, потом 5-сек чанки ---
                block_start = 0.0
                while block_start < duration:
                    block_duration = min(block_sec, duration - block_start)

                    block_path = os.path.join(tmpdir, f"{uuid.uuid4().hex}_block.ogg")
                    created_files.append(block_path)

                    await asyncio.to_thread(
                        cut_segment, audio_path, block_path, block_start, block_duration
                    )

                    async for piece in _yield_chunks(
                        client, block_path, 0.0, block_duration, chunk_sec,
                        tmpdir, created_files, accumulated_text
                    ):
                        accumulated_text = piece["full"]
                        yield piece["new"]

                    # удаляем минутный блок сразу после обработки
                    _safe_remove(block_path)

                    block_start += block_sec

        except Exception as exc:
            logger.exception("Ошибка при транскрипции: %s", exc)
            raise
        finally:
            for f in created_files:
                _safe_remove(f)
            _safe_rmdir(tmpdir)


# ---------------------------------------------------------------------------
# Внутренние вспомогательные функции
# ---------------------------------------------------------------------------

async def _yield_chunks(
    client: httpx.AsyncClient,
    audio_path: str,
    offset: float,
    total_duration: float,
    chunk_sec: int,
    tmpdir: str,
    created_files: list,
    initial_text: str,
) -> AsyncIterator[dict]:
    """
    Нарезает audio_path на chunk_sec-секундные фрагменты начиная с offset,
    транскрибирует каждый и yield-ит dict{"new": str, "full": str}.
    """
    accumulated = initial_text
    start = offset

    while start < offset + total_duration:
        seg_duration = min(chunk_sec, (offset + total_duration) - start)
        if seg_duration < 0.5:
            break

        chunk_path = os.path.join(tmpdir, f"{uuid.uuid4().hex}_chunk.ogg")
        created_files.append(chunk_path)

        await asyncio.to_thread(cut_segment, audio_path, chunk_path, start, seg_duration)

        new_text = await transcribe_chunk(client, chunk_path, accumulated)

        _safe_remove(chunk_path)

        if new_text:
            # Склейка: если накоплен текст, добавляем пробел на стыке
            joined = _join(accumulated, new_text)
            yield {"new": new_text, "full": joined}
            accumulated = joined

        start += seg_duration


def _join(left: str, right: str) -> str:
    """Склеивает два текстовых фрагмента с правильным пробелом."""
    if not left:
        return right
    if not right:
        return left
    # Если левая часть заканчивается на знак препинания — просто пробел
    return left.rstrip() + " " + right.lstrip()


def _safe_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        logger.warning("Не удалось удалить файл %s: %s", path, e)


def _safe_rmdir(path: str) -> None:
    try:
        if os.path.isdir(path):
            os.rmdir(path)
    except OSError as e:
        logger.warning("Не удалось удалить директорию %s: %s", path, e)
