"""Небольшая нормализация текста после Whisper без изменения распознанных слов."""

from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_START_RE = re.compile(r"(^|(?<=[.!?…])\s+)([\"'«„“‘(\[]*)([a-zа-яё])")


def restore_punctuation(text: str, ensure_final: bool = False) -> str:
    """Восстанавливает заглавные буквы и, при необходимости, финальную точку.

    Основную пунктуацию ставит Whisper на полном контексте фразы. Эта функция
    лишь исправляет типичные пропуски и не заменяет слова предположениями.
    """
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if not text:
        return ""

    text = _SENTENCE_START_RE.sub(
        lambda match: match.group(1) + match.group(2) + match.group(3).upper(),
        text,
    )
    if ensure_final and text[-1].isalnum():
        text += "."
    return text
