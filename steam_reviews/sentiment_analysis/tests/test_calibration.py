from __future__ import annotations

from sentiment_analysis.calibration.compare_calibration import cohen_kappa, summarize
from sentiment_analysis.calibration.llm_calibrate import parse_label, prompt_for


def test_parse_label_plain_and_fenced_json():
    assert parse_label('{"label":"positive","reason":"ok"}') == ("positive", "ok")
    assert parse_label('```json\n{"label":"negative","reason":"bad"}\n```') == ("negative", "bad")
    assert parse_label('prefix {"label":"neutral"} suffix')[0] == "neutral"


def test_parse_label_rejects_unknown_and_garbage():
    assert parse_label('{"label":"mixed"}')[0] is None
    assert parse_label("not json")[0] is None


def test_prompt_does_not_include_vader_label_or_score():
    prompt = prompt_for({
        "entity_name": "VAC",
        "text_clean": "VAC is bad.",
        "vader_label": "negative",
        "vader_score": -0.5,
    })
    assert "VAC is bad." in prompt
    assert "vader_label" not in prompt.lower()
    assert "-0.5" not in prompt


def test_confusion_and_kappa():
    pairs = [("positive", "positive"), ("negative", "negative"), ("neutral", "neutral")]
    result = summarize(pairs)
    assert result["n"] == 3
    assert result["agreement"] == 1.0
    assert result["kappa"] == 1.0
    assert result["confusion"]["positive"]["positive"] == 1
    assert cohen_kappa([]) is None
