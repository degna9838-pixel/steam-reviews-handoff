from __future__ import annotations

import pytest

from sentiment_analysis.evaluate import classification_metrics, evaluate_rows


def test_perfect_classification():
    result = classification_metrics([
        ("positive", "positive"),
        ("negative", "negative"),
        ("neutral", "neutral"),
    ])
    assert result["accuracy"] == 1.0
    assert result["macro_f1"] == 1.0
    assert result["kappa"] == 1.0


def test_known_confusion_and_macro_f1():
    result = classification_metrics([
        ("positive", "positive"),
        ("negative", "negative"),
        ("neutral", "positive"),
    ])
    assert result["accuracy"] == round(2 / 3, 6)
    assert result["per_class"]["positive"]["f1"] == round(2 / 3, 6)
    assert result["per_class"]["negative"]["f1"] == 1.0
    assert result["per_class"]["neutral"]["f1"] == 0.0
    assert result["macro_f1"] == round((2 / 3 + 1.0) / 3, 6)
    assert result["confusion"]["neutral"]["positive"] == 1
    assert result["kappa"] == pytest.approx(0.5)


def test_missing_labels_are_counted_not_silently_dropped():
    report = evaluate_rows(
        [
            {"sample_id": "1", "entity_id": "game", "human_label": "positive", "vader_label": "positive"},
            {"sample_id": "2", "entity_id": "game", "human_label": "", "vader_label": "negative"},
            {"sample_id": "3", "entity_id": "game", "human_label": "neutral", "vader_label": "mixed"},
        ],
        gold_field="human_label",
        pred_field="vader_label",
        entity_field="entity_id",
    )
    assert report["rows_read"] == 3
    assert report["overall"]["n"] == 1
    assert report["errors"]["missing_or_invalid_gold"] == 1
    assert report["errors"]["missing_or_invalid_prediction"] == 1
    assert report["per_entity"]["game"]["n"] == 1
