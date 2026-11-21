"""Создание ASS-субтитров исключительно по временным меткам Whisper."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechSegment:
    """Фрагмент речи с таймингами, полученными от API (не вычисленными вручную)."""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    duration: float
    has_audio: bool
    has_video: bool


def wrap_subtitle(text: str, max_chars: int = 28) -> str:
    """Переносит фразу максимум на две строки, не разрывая слова.

    Whisper возвращает достаточно короткие сегменты. Здесь намеренно нет
    разбиения одного сегмента на несколько: для этого потребовались бы
    выдуманные тайминги. ASS автоматически масштабирует слишком длинный текст.
    """
    words = text.split()
    if not words:
        return ""
    if len(text) <= max_chars:
        return text

    # Одна очень длинная строка не помещается в кружок: балансируем две строки.
    target = len(text) / 2
    candidates: list[tuple[float, int]] = []
    current = 0
    for index, word in enumerate(words[:-1]):
        current += len(word) + (1 if index else 0)
        candidates.append((abs(current - target), index + 1))
    if not candidates:
        return text
    split_at = min(candidates)[1]
    first = " ".join(words[:split_at])
    second = " ".join(words[split_at:])
    return first + "\\N" + second


def _ass_time(seconds: float) -> str:
    # Округление только до точности ASS (10 мс); длительность речи не продлевается.
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, cents = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cents:02d}"


def _ass_escape(text: str) -> str:
    return (
        text.replace("\\", "＼")
        .replace("{", "（")
        .replace("}", "）")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def build_ass(
    segments: list[SpeechSegment],
    width: int,
    height: int,
    duration: float,
    font_name: str = "DejaVu Sans",
    font_scale: float = 0.055,
    max_chars: int = 28,
) -> str:
    """Строит стилизованный ASS; пустые и вышедшие за видео сегменты пропускает."""
    side = min(width, height)
    font_size = max(18, round(side * font_scale))
    margin = max(18, round(side * 0.075))
    outline = max(2, round(side * 0.005))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        # Полупрозрачная подложка (BorderStyle=3), белый текст и тонкая обводка.
        f"Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,"
        f"&H00101010,&H78000000,0,0,0,0,100,100,0,0,3,{outline},0,"
        f"2,{margin},{margin},{margin},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]
    for segment in segments:
        text = segment.text.strip()
        if not text or segment.end <= 0 or segment.start >= duration:
            continue
        start = max(0.0, segment.start)
        end = min(duration, segment.end)
        if end <= start:
            continue
        wrapped = wrap_subtitle(_ass_escape(text), max_chars)
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{wrapped}"
        )
    return "\n".join(lines) + "\n"
