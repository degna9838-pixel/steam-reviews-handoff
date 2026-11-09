from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

LABELS = ("positive", "negative", "neutral")


def read_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def cohen_kappa(pairs):
    total = len(pairs)
    if not total:
        return None
    observed = sum(1 for a, b in pairs if a == b) / total
    count_a = Counter(a for a, _ in pairs)
    count_b = Counter(b for _, b in pairs)
    expected = sum((count_a[label] / total) * (count_b[label] / total) for label in LABELS)
    if expected >= 1.0:
        return None
    return (observed - expected) / (1.0 - expected)


def summarize(pairs):
    total = len(pairs)
    if not total:
        return {"n": 0, "agreement": None, "kappa": None, "confusion": {}}
    confusion = {actual: {predicted: 0 for predicted in LABELS} for actual in LABELS}
    for actual, predicted in pairs:
        confusion[actual][predicted] += 1
    return {
        "n": total,
        "agreement": sum(1 for a, b in pairs if a == b) / total,
        "kappa": cohen_kappa(pairs),
        "confusion": confusion,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    samples = {row["sample_id"]: row for row in read_jsonl(args.input)}
    predictions = read_jsonl(args.predictions)
    pairs = []
    per_entity = defaultdict(list)
    errors = {"api_error": 0, "parse_error": 0, "missing_sample": 0}
    for row in predictions:
        sample = samples.get(row.get("sample_id"))
        if sample is None:
            errors["missing_sample"] += 1
            continue
        actual = sample.get("vader_label")
        predicted = row.get("llm_label")
        if predicted not in LABELS:
            if row.get("http_status") not in (200, None):
                errors["api_error"] += 1
            else:
                errors["parse_error"] += 1
            continue
        if actual not in LABELS:
            continue
        pairs.append((actual, predicted))
        per_entity[row.get("entity_id")].append((actual, predicted))
    report = {
        "schema_version": "sentiment-calibration-1.0",
        "input": str(Path(args.input)),
        "predictions": str(Path(args.predictions)),
        "samples_read": len(samples),
        "predictions_read": len(predictions),
        "errors": errors,
        "overall": summarize(pairs),
        "per_entity": {entity: summarize(items) for entity, items in sorted(per_entity.items())},
        "note": "VADER is compared with an LLM's entity-specific label; this is sample agreement, not human ground truth.",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"n": report["overall"]["n"], "agreement": report["overall"]["agreement"],
                      "kappa": report["overall"]["kappa"], "errors": errors}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
