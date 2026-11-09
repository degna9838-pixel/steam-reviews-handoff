from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .calibration.compare_calibration import cohen_kappa

LABELS = ("positive", "negative", "neutral")


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def classification_metrics(pairs: list[tuple[str, str]]) -> dict:
    total = len(pairs)
    if not total:
        return {
            "n": 0,
            "accuracy": None,
            "macro_f1": None,
            "kappa": None,
            "confusion": {},
            "per_class": {},
        }
    confusion = {actual: {predicted: 0 for predicted in LABELS} for actual in LABELS}
    for actual, predicted in pairs:
        confusion[actual][predicted] += 1
    correct = sum(confusion[label][label] for label in LABELS)
    per_class = {}
    for label in LABELS:
        true_positive = confusion[label][label]
        false_positive = sum(confusion[actual][label] for actual in LABELS if actual != label)
        false_negative = sum(confusion[label][predicted] for predicted in LABELS if predicted != label)
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {
            "support": sum(confusion[label].values()),
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
        }
    macro_f1 = sum(per_class[label]["f1"] for label in LABELS) / len(LABELS)
    return {
        "n": total,
        "accuracy": round(correct / total, 6),
        "macro_f1": round(macro_f1, 6),
        "kappa": cohen_kappa(pairs),
        "confusion": confusion,
        "per_class": per_class,
    }


def evaluate_rows(rows: list[dict], *, gold_field: str, pred_field: str, entity_field: str) -> dict:
    pairs = []
    per_entity: dict[str, list[tuple[str, str]]] = {}
    errors = Counter()
    for row in rows:
        gold = row.get(gold_field)
        predicted = row.get(pred_field)
        if gold not in LABELS:
            errors["missing_or_invalid_gold"] += 1
            continue
        if predicted not in LABELS:
            errors["missing_or_invalid_prediction"] += 1
            continue
        pair = (gold, predicted)
        pairs.append(pair)
        entity = row.get(entity_field) or "unknown"
        per_entity.setdefault(entity, []).append(pair)
    return {
        "schema_version": "sentiment-evaluation-1.0",
        "rows_read": len(rows),
        "gold_field": gold_field,
        "prediction_field": pred_field,
        "errors": dict(errors),
        "overall": classification_metrics(pairs),
        "per_entity": {
            entity: classification_metrics(items) for entity, items in sorted(per_entity.items())
        },
        "note": "Gold labels must be human/independent labels; VADER or LLM labels alone are not ground truth.",
    }


def evaluate_file(input_path: Path, *, gold_field: str, pred_field: str, entity_field: str, out: Path) -> dict:
    report = evaluate_rows(
        read_jsonl(input_path),
        gold_field=gold_field,
        pred_field=pred_field,
        entity_field=entity_field,
    )
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report
