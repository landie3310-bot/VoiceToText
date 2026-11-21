"""Отдельный конвейер видеокружков: Groq verbose_json + FFmpeg/ASS."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import subprocess
from pathlib import Path

import httpx

import config
from punctuation import restore_punctuation
from subtitles import SpeechSegment, VideoInfo, build_ass, group_words

logger = logging.getLogger(__name__)


class VideoProcessingError(RuntimeError):
    """Понятная ошибка обработки без прекращения работы бота."""


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=config.FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoProcessingError(
            f"{command[0]} превысил лимит {config.FFMPEG_TIMEOUT_SECONDS} сек."
        ) from exc
    except OSError as exc:
        raise VideoProcessingError(f"Не удалось запустить {command[0]}: {exc}") from exc
    if result.returncode:
        error = result.stderr.decode(errors="replace")[-4000:]
        raise VideoProcessingError(
            f"{command[0]} завершился с кодом {result.returncode}: {error}"
        )
    return result


async def probe_video(path: Path) -> VideoInfo:
    def probe() -> VideoInfo:
        result = _run([
            "ffprobe", "-v", "error", "-show_format", "-show_streams",
            "-of", "json", str(path),
        ])
        try:
            data = json.loads(result.stdout)
            streams = data["streams"]
            video = next(s for s in streams if s.get("codec_type") == "video")
            width = int(video["width"])
            height = int(video["height"])
            duration = float(data["format"]["duration"])
        except (ValueError, TypeError, KeyError, StopIteration, json.JSONDecodeError) as exc:
            raise VideoProcessingError("FFprobe вернул некорректные метаданные видео.") from exc
        if width <= 0 or height <= 0 or not math.isfinite(duration) or duration <= 0:
            raise VideoProcessingError("Некорректное разрешение или длительность видео.")
        return VideoInfo(
            width=width,
            height=height,
            duration=duration,
            has_audio=any(s.get("codec_type") == "audio" for s in streams),
            has_video=True,
        )

    return await asyncio.to_thread(probe)


def extract_audio(source: Path, destination: Path) -> None:
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-map", "0:a:0", "-vn", "-ar", "16000", "-ac", "1",
        "-c:a", "libopus", "-b:a", "24k", "-threads", "1", str(destination),
    ])


async def transcribe_segments(audio_path: Path) -> list[SpeechSegment]:
    """Получает только фактические сегменты Groq; текстовый fallback не допускается."""
    # Минутный OGG/Opus 32 кбит/с существенно меньше лимита Groq в 25 МБ.
    if audio_path.stat().st_size > 24 * 1024 * 1024:
        raise VideoProcessingError("Аудиодорожка превышает лимит Groq в 25 МБ.")

    timeout = httpx.Timeout(config.GROQ_TIMEOUT_SECONDS)
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(config.GROQ_RETRIES):
            try:
                with audio_path.open("rb") as audio:
                    response = await client.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                        files={"file": (audio_path.name, audio, "audio/ogg")},
                        data={
                            "model": config.VIDEO_WHISPER_MODEL,
                            "response_format": "verbose_json",
                            "language": "ru",
                            "prompt": config.WHISPER_PROMPT,
                            "timestamp_granularities[]": ["segment", "word"],
                            "temperature": "0",
                        },
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                if response.status_code >= 400:
                    detail = response.text[:1000]
                    raise VideoProcessingError(
                        f"Groq отклонил запрос ({response.status_code}): {detail}"
                    )
                payload = response.json()
                if payload.get("words"):
                    return group_words(
                        payload["words"],
                        max_line_chars=config.SUBTITLE_MAX_CHARS_PER_LINE,
                    )
                # Старый/кастомный endpoint без word-таймингов: ничего не выдумываем.
                return _parse_segments(payload)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt + 1 >= config.GROQ_RETRIES:
                    break
                delay = min(8.0, 2.0 ** attempt)
                retry_after = None
                if isinstance(exc, httpx.HTTPStatusError):
                    retry_after = exc.response.headers.get("retry-after")
                if retry_after:
                    try:
                        delay = min(30.0, max(delay, float(retry_after)))
                    except ValueError:
                        pass
                logger.warning("Groq: попытка %d не удалась: %s", attempt + 1, exc)
                await asyncio.sleep(delay)
            except (ValueError, KeyError, TypeError) as exc:
                raise VideoProcessingError("Groq вернул неожиданный формат ответа.") from exc
    raise VideoProcessingError(f"Groq недоступен после повторных попыток: {last_error}")


def _parse_segments(payload: dict) -> list[SpeechSegment]:
    raw_segments = payload.get("segments")
    if raw_segments is None:
        raise VideoProcessingError(
            "Groq не вернул segments. Проверьте поддержку verbose_json выбранной моделью."
        )
    result: list[SpeechSegment] = []
    previous_end = 0.0
    for item in raw_segments:
        if not isinstance(item, dict):
            raise VideoProcessingError("Некорректный сегмент в ответе Groq.")
        start = float(item["start"])
        end = float(item["end"])
        text = restore_punctuation(str(item.get("text", "")), ensure_final=True)
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise VideoProcessingError("Groq вернул некорректные временные метки.")
        # Не выдумываем метки, лишь исключаем редкое перекрытие ответа API.
        start = max(start, previous_end)
        if text and end > start:
            result.append(SpeechSegment(start=start, end=end, text=text))
            previous_end = end
    return result


def build_ffmpeg_command(
    source: Path,
    destination: Path,
    info: VideoInfo,
) -> list[str]:
    """Формирует команду для проверки аргументов без запуска FFmpeg."""
    side = min(info.width, info.height)
    side = max(2, side - side % 2)
    audio_args = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "96k"] if info.has_audio else ["-an"]
    performance_args = [
        "-threads", str(config.VIDEO_THREADS), "-filter_threads", "1",
    ]
    common = [
        *audio_args,
        "-c:v", "libx264", "-preset", config.VIDEO_PRESET,
        "-crf", str(config.VIDEO_CRF), "-pix_fmt", "yuv420p",
        *performance_args,
        "-movflags", "+faststart", "-t", f"{info.duration:.3f}",
        str(destination),
    ]
    if info.width == info.height:
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-map", "0:v:0",
            "-vf", "subtitles=subtitles.ass",
            *common,
        ]
    # Весь неквадратный исходный кадр сохраняется поверх размытого фона.
    filter_complex = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={side}:{side}:force_original_aspect_ratio=increase,"
        f"crop={side}:{side},boxblur=20:1[background];"
        f"[fg]scale={side}:{side}:force_original_aspect_ratio=decrease[foreground];"
        "[background][foreground]overlay=(W-w)/2:(H-h)/2,subtitles=subtitles.ass[v]"
    )
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-filter_complex", filter_complex,
        "-map", "[v]", *common,
    ]


async def burn_subtitles(
    source: Path,
    destination: Path,
    workdir: Path,
    segments: list[SpeechSegment],
    info: VideoInfo,
) -> None:
    """Встраивает субтитры, сохраняет звук и не обрезает исходное изображение."""
    ass_path = workdir / "subtitles.ass"
    ass_path.write_text(
        build_ass(
            segments,
            info.width,
            info.height,
            info.duration,
            font_name=config.SUBTITLE_FONT,
            font_scale=config.SUBTITLE_FONT_SCALE,
            max_chars=config.SUBTITLE_MAX_CHARS_PER_LINE,
        ),
        encoding="utf-8",
    )
    command = build_ffmpeg_command(source, destination, info)
    await asyncio.to_thread(_run, command, workdir)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise VideoProcessingError("FFmpeg не создал выходной файл.")


async def process_video_note(source: Path, workdir: Path) -> tuple[Path, VideoInfo, Path | None]:
    """Возвращает (готовое_видео, метаданные, оригинал_при_отсутствии_речи)."""
    info = await probe_video(source)
    if info.duration > config.MAX_VIDEO_SECONDS + 0.1:
        raise VideoProcessingError(
            f"Видео длиннее {config.MAX_VIDEO_SECONDS} сек. и не может быть видеокружком."
        )
    if not info.has_audio:
        return source, info, source
    audio = workdir / "audio.ogg"
    await asyncio.to_thread(extract_audio, source, audio)
    segments = await transcribe_segments(audio)
    if not segments:
        return source, info, source
    output = workdir / "result.mp4"
    await burn_subtitles(source, output, workdir, segments, info)
    return output, info, None
