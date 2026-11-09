from __future__ import annotations

import json

from sentiment_analysis.aggregate import summarize_game
from sentiment_analysis.llm import export_jobs, import_predictions
from sentiment_analysis.sentiment import SentimentAnalyzer
from sentiment_analysis.settings import ALGORITHM_VERSION
from sentiment_analysis.store import analysis_rows, save_batch
from sentiment_analysis.util import parse_utc

from .helpers import batch, record, setup

NOW = parse_utc("2026-11-09T12:00:00Z")


def write_prediction(path, item, entity_id, label="negative", reason="test"):
    path.write_text(json.dumps({
        "job_id": f"730:{item['review_id']}:{item['revision']}:{entity_id}:llm-entity-v1",
        "app_id": 730,
        "review_id": item["review_id"],
        "revision": item["revision"],
        "entity_id": entity_id,
        "label": label,
        "reason": reason,
        "raw_content": "{}",
        "error": None,
    }) + "\n", encoding="utf-8")
    return path


def prepare(tmp_path):
    db, config, analyzer = setup(tmp_path)
    item = record(text="CS2 is fun but VAC is bad.")
    save_batch(db, batch(item), 0, app_id=730, config=config,
               analyzer=analyzer, algorithm_version=ALGORITHM_VERSION)
    assert {row["entity_id"] for row in analysis_rows(db, ALGORITHM_VERSION)} == {"game", "anticheat"}
    return db, config, item


def test_import_llm_overrides_matching_vader_row(tmp_path):
    db, config, item = prepare(tmp_path)
    predictions = write_prediction(tmp_path / "pred.jsonl", item, "game", label="negative")
    result = import_predictions(db, config, predictions, app_id=730)
    assert result["imported"] == 1
    rows = {row["entity_id"]: row for row in db.execute(
        "SELECT entity_id,sentiment_label,scorer FROM effective_analysis"
    ).fetchall()}
    assert rows["game"]["sentiment_label"] == "negative"
    assert rows["game"]["scorer"] == "llm"
    assert rows["anticheat"]["scorer"] == "vader"


def test_export_jobs_skips_labeled_rows(tmp_path):
    db, config, item = prepare(tmp_path)
    assert export_jobs(db, config, tmp_path / "jobs1.jsonl", app_id=730) == 2
    import_predictions(db, config, write_prediction(tmp_path / "pred.jsonl", item, "game"), app_id=730)
    assert export_jobs(db, config, tmp_path / "jobs2.jsonl", app_id=730) == 1
    assert export_jobs(db, config, tmp_path / "jobs3.jsonl", app_id=730, include_labeled=True) == 2


def test_summary_full_coverage_uses_llm(tmp_path):
    db, config, item = prepare(tmp_path)
    path = tmp_path / "preds.jsonl"
    with path.open("w", encoding="utf-8") as stream:
        for entity_id, label in (("game", "negative"), ("anticheat", "positive")):
            row = {
                "job_id": f"job-{entity_id}", "app_id": 730, "review_id": item["review_id"],
                "revision": 1, "entity_id": entity_id, "label": label, "reason": "test",
                "raw_content": "{}", "error": None,
            }
            stream.write(json.dumps(row) + "\n")
    import_predictions(db, config, path, app_id=730)
    summary = summarize_game(db, config, {}, now=NOW, window_hours=24, baseline_hours=24, min_mentions=1)
    game = next(item for item in summary["entities"] if item["entity_id"] == "game")
    assert game["current"]["negative_count"] == 1
    assert summary["llm_coverage"] == 1.0
    assert summary["data_status"] == "ok"
    assert summary["scorer_counts"]["llm"] == 2


def test_summary_partial_coverage_is_marked(tmp_path):
    db, config, item = prepare(tmp_path)
    import_predictions(db, config, write_prediction(tmp_path / "pred.jsonl", item, "game"), app_id=730)
    summary = summarize_game(db, config, {}, now=NOW, window_hours=24, baseline_hours=24, min_mentions=1)
    assert summary["llm_coverage"] == 0.5
    assert summary["data_status"] == "llm_partial"
    assert summary["provisional"] is True


def test_invalid_or_unknown_entity_is_error(tmp_path):
    db, config, item = prepare(tmp_path)
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({
        "job_id": "bad", "app_id": 730, "review_id": item["review_id"], "revision": 1,
        "entity_id": "nope", "label": "negative", "reason": "x", "raw_content": "{}", "error": None,
    }) + "\n", encoding="utf-8")
    result = import_predictions(db, config, path, app_id=730)
    assert result["imported"] == 0
    assert result["errors"] == 1
    assert db.execute("SELECT COUNT(*) FROM llm_analysis").fetchone()[0] == 0


def test_stale_prediction_is_skipped(tmp_path):
    db, config, item = prepare(tmp_path)
    stale = dict(item)
    stale["revision"] = 1
    current = record(revision=2)
    save_batch(db, batch(current, change_id=2), 1, app_id=730, config=config,
               analyzer=SentimentAnalyzer(), algorithm_version=ALGORITHM_VERSION)
    result = import_predictions(db, config, write_prediction(tmp_path / "stale.jsonl", stale, "game"), app_id=730)
    assert result["skipped_stale"] == 1
    assert result["imported"] == 0
