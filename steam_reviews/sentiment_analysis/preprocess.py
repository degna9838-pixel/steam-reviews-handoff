from __future__ import annotations

import re
import unicodedata

STEAM_TAG = re.compile(r"\[/?[A-Za-z][^\]]*\]")
URL = re.compile(r"https?://\S+", re.IGNORECASE)
ZERO_WIDTH = re.compile(r"[​-‏‪-‮﻿]")
SENTENCE = re.compile(r"(?<=[.!?])\s+")


def normalize_llm_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = ZERO_WIDTH.sub("", text)
    text = text.replace("’", "'").replace("‘", "'").replace("ʼ", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = STEAM_TAG.sub(" ", text)
    text = URL.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clip_middle(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    half = max(1, max_chars // 2)
    return text[:half] + " ... " + text[-half:]


def _match_windows(text: str, entity, max_chars: int) -> str:
    spans = [match.span() for match in entity.regex.finditer(text)]
    if not spans:
        return clip_middle(text, max_chars)
    pieces = []
    for start, end in spans:
        left = max(0, start - max_chars // 2)
        right = min(len(text), end + max_chars // 2)
        piece = text[left:right].strip()
        if piece and piece not in pieces:
            pieces.append(piece)
    return " ... ".join(pieces)


def entity_context(text: str, entity, max_chars: int = 900) -> str:
    text = normalize_llm_text(text)
    if not text:
        return ""
    if entity.scope == "document":
        return clip_middle(text, max_chars)
    sentences = [sentence for sentence in SENTENCE.split(text) if sentence.strip()]
    if len(sentences) <= 1:
        return _match_windows(text, entity, max_chars)
    hits = {index for index, sentence in enumerate(sentences) if entity.regex.search(sentence)}
    if not hits:
        return _match_windows(text, entity, max_chars)
    selected = set()
    for index in hits:
        for neighbour in (index - 1, index, index + 1):
            if 0 <= neighbour < len(sentences):
                selected.add(neighbour)
    context = " ".join(sentences[index] for index in sorted(selected))
    if len(context) > max_chars:
        context = _match_windows(text, entity, max_chars)
    return context
