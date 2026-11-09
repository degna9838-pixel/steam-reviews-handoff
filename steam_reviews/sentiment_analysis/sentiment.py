from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .entities import Entity, EntityMatch
from .settings import LEXICON_PATH

POSITIVE_THRESHOLD = 0.05
NEGATIVE_THRESHOLD = -0.05


class SentimentAnalyzer:
    def __init__(self, lexicon_path: Path | None = None):
        self.analyzer = SentimentIntensityAnalyzer()
        path = Path(lexicon_path) if lexicon_path else LEXICON_PATH
        payload_bytes = path.read_bytes()
        self.lexicon_hash = hashlib.sha256(payload_bytes).hexdigest()
        payload = json.loads(payload_bytes.decode("utf-8"))
        weights = payload.get("weights", {})
        if not isinstance(weights, dict):
            raise ValueError("Domain lexicon requires a weights object")
        cleaned = {}
        for word, value in weights.items():
            if not isinstance(word, str) or not word:
                raise ValueError("Domain lexicon words must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Invalid lexicon weight for {word!r}")
            if not -4.0 <= float(value) <= 4.0:
                raise ValueError(f"Lexicon weight out of range for {word!r}")
            cleaned[word.lower()] = float(value)
        self.analyzer.lexicon.update(cleaned)
        self.version = str(payload.get("version", "unknown"))

    @staticmethod
    def normalize(text: str) -> str:
        text = text.replace("’", "'").replace("‘", "'").replace("ʼ", "'")
        text = re.sub(r"pay[\s-]*to[\s-]*win", "p2w", text, flags=re.IGNORECASE)
        text = re.sub(r"anti[\s-]*cheat", "anticheat", text, flags=re.IGNORECASE)
        return text

    def score_text(self, text: str) -> float:
        if not text or not text.strip():
            return 0.0
        return float(self.analyzer.polarity_scores(self.normalize(text))["compound"])

    @staticmethod
    def label_for(score: float) -> str:
        if score >= POSITIVE_THRESHOLD:
            return "positive"
        if score <= NEGATIVE_THRESHOLD:
            return "negative"
        return "neutral"

    def score_match(self, match: EntityMatch) -> tuple[float, str]:
        if match.entity.scope == "document":
            score = self.score_text(match.evidence)
        else:
            scores = [self.score_text(clause) for clause in match.clauses]
            score = sum(scores) / len(scores) if scores else 0.0
        return score, self.label_for(score)


def entity_signature(entity: Entity) -> dict:
    return entity.as_dict()
