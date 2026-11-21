"""Безопасные ASS-субтитры и группировка только по реальным word-таймингам."""

from __future__ import annotations

from dataclasses import dataclass

from punctuation import restore_punctuation


@dataclass(frozen=True)
class SpeechSegment:
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


def effective_max_chars(width: int, font_size: int, requested: int = 20) -> int:
    """Консервативный лимит: ~72% ширины доступно для текста внутри круга."""
    return min(requested, max(12, round(width * 0.72 / (font_size * 0.58))))


def wrap_subtitle(text: str, max_chars: int = 20) -> str:
    """Жёстко переносит строки по лимиту; никогда не создаёт больше двух строк."""
    words = text.split()
    if not words:
        return ""
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) <= 2:
        return "\\N".join(lines)

    # Fallback для ответа без word-таймингов: весь текст сохраняется в двух
    # строках, а build_ass дополнительно уменьшит его по фактической длине.
    target = len(text) / 2
    best = min(
        range(1, len(words)),
        key=lambda i: abs(len(" ".join(words[:i])) - target),
    )
    return " ".join(words[:best]) + "\\N" + " ".join(words[best:])


def group_words(
    words: list[dict],
    max_line_chars: int = 20,
    max_words: int = 6,
) -> list[SpeechSegment]:
    """Создаёт короткие реплики; начало/конец берутся у первого/последнего слова."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    length = 0
    for word in words:
        token = str(word.get("word", ""))
        clean = token.strip()
        if not clean:
            continue
        added = len(clean) + (1 if current else 0)
        if current and (
            len(current) >= max_words
            or length + added > max_line_chars * 2
        ):
            groups.append(current)
            current, length = [], 0
            added = len(clean)
        current.append(word)
        length += added
        # Паузы/пунктуация — естественная граница смысловой реплики.
        if clean.endswith((".", "!", "?", "…")) or (
            clean.endswith((",", ";", ":")) and len(current) >= 2
        ):
            groups.append(current)
            current, length = [], 0
    if current:
        groups.append(current)

    result: list[SpeechSegment] = []
    for group in groups:
        text = " ".join(str(item["word"]).strip() for item in group).strip()
        text = restore_punctuation(text, ensure_final=True)
        result.append(SpeechSegment(
            start=float(group[0]["start"]),
            end=float(group[-1]["end"]),
            text=text,
        ))
    return result


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, cents = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cents:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "＼").replace("{", "（").replace("}", "）").replace("\r", " ").replace("\n", " ")


def build_ass(
    segments: list[SpeechSegment], width: int, height: int, duration: float,
    font_name: str = "DejaVu Sans", font_scale: float = 0.046,
    max_chars: int = 20,
) -> str:
    side = min(width, height)
    # Без прежнего min(18): для кружка 240 px это было слишком крупно.
    font_size = max(12, round(side * font_scale))
    margin = max(16, round(side * 0.11))
    bottom_margin = max(20, round(side * 0.085))
    outline = max(1, round(side * 0.003))
    line_limit = effective_max_chars(side, font_size, max_chars)
    lines = [
        "[Script Info]", "ScriptType: v4.00+",
        f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        # BorderStyle=1: обводка, без огромной прямоугольной плашки.
        f"Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,"
        f"&H00101010,&H78000000,0,0,0,0,100,100,0,0,1,{outline},1,"
        f"2,{margin},{margin},{bottom_margin},1", "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for segment in segments:
        text = segment.text.strip()
        if not text or segment.end <= 0 or segment.start >= duration:
            continue
        start, end = max(0.0, segment.start), min(duration, segment.end)
        if end <= start:
            continue
        wrapped = wrap_subtitle(_ass_escape(text), line_limit)
        longest = max(map(len, wrapped.split("\\N")), default=0)
        # Аварийная страховка для очень длинного segment-only ответа.
        scale_x = min(100, max(30, round(100 * line_limit / max(1, longest))))
        override = "" if scale_x == 100 else "{\\fscx" + str(scale_x) + "}"
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{override}{wrapped}"
        )
    return "\n".join(lines) + "\n"
