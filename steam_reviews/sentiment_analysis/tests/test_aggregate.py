from __future__ import annotations

from sentiment_analysis.aggregate import (
    chi_square_independence,
    export_dashboard,
    query_window,
    summarize_game,
)
from sentiment_analysis.settings import ALGORITHM_VERSION
from sentiment_analysis.store import state_set
from sentiment_analysis.util import parse_utc

from .helpers import insert_analysis, setup_empty_state

NOW = parse_utc("2026-11-09T12:00:00Z")


def test_query_window_uses_start_inclusive_end_exclusive(tmp_path):
    db, config = setup_empty_state(tmp_path)
    insert_analysis(db, "a", "game", "positive", "2026-11-09T11:00:00Z")
    insert_analysis(db, "b", "game", "negative", "2026-11-09T11:30:00Z")
    insert_analysis(db, "c", "game", "neutral", "2026-11-09T12:00:00Z")
    db.commit()
    counts, versions = query_window(
        db, "game", parse_utc("2026-11-09T11:00:00Z"), NOW, ALGORITHM_VERSION
    )
    assert counts["mention_count"] == 2
    assert counts["positive_count"] == 1
    assert counts["negative_count"] == 1
    assert versions == ["clean-en-v3"]


def test_chi_square_reports_conditions_and_significance():
    current = {"positive_count": 10, "negative_count": 0, "neutral_count": 0}
    baseline = {"positive_count": 0, "negative_count": 10, "neutral_count": 0}
    result = chi_square_independence(current, baseline)
    assert result["conditions_met"] is True
    assert result["significant"] is True
    assert result["p_value"] < 0.05
    small = chi_square_independence(
        {"positive_count": 1, "negative_count": 0, "neutral_count": 0},
        {"positive_count": 0, "negative_count": 1, "neutral_count": 0},
    )
    assert small["conditions_met"] is False
    assert small["p_value"] is None


def test_summary_detects_statistical_change_and_labels_backfill(tmp_path):
    db, config = setup_empty_state(tmp_path)
    for index in range(10):
        insert_analysis(db, f"cur-{index}", "game", "positive", "2026-11-09T11:30:00Z")
        insert_analysis(db, f"base-{index}", "game", "negative", "2026-11-08T20:00:00Z")
    db.commit()
    summary = summarize_game(
        db, config, {"backfill_status": "running"}, now=NOW,
        window_hours=1, baseline_hours=24, min_mentions=5,
    )
    game = next(item for item in summary["entities"] if item["entity_id"] == "game")
    assert game["current"]["mention_count"] == 10
    assert game["baseline"]["mention_count"] == 10
    assert game["change"] == 2.0
    assert game["change_flag"] is True
    assert game["alert_type"] == "statistical"
    assert summary["data_status"] == "backfill_incomplete"


def test_summary_does_not_turn_missing_data_into_zero(tmp_path):
    db, config = setup_empty_state(tmp_path)
    summary = summarize_game(db, config, {}, now=NOW)
    game = next(item for item in summary["entities"] if item["entity_id"] == "game")
    assert game["current"]["metric"] is None
    assert game["current"]["mention_count"] == 0
    assert game["data_status"] == "insufficient_window"
    assert summary["data_status"] == "no_data"


def test_sample_mode_and_mixed_pipeline_status(tmp_path):
    db, config = setup_empty_state(tmp_path)
    state_set(db, "mode", "sample")
    insert_analysis(db, "a", "game", "positive", "2026-11-09T11:30:00Z")
    db.commit()
    summary = summarize_game(db, config, {}, now=NOW)
    assert summary["data_status"] == "sample"

    state_set(db, "mode", "live")
    insert_analysis(db, "b", "game", "negative", "2026-11-09T11:31:00Z",
                    pipeline_version="clean-en-v2")
    db.commit()
    summary = summarize_game(db, config, {}, now=NOW)
    assert summary["data_status"] == "mixed_pipeline_versions"


def test_stale_source_status(tmp_path):
    db, config = setup_empty_state(tmp_path)
    insert_analysis(db, "a", "game", "positive", "2026-11-09T11:30:00Z")
    db.commit()
    summary = summarize_game(
        db, config, {"last_success_at": "2026-11-09T00:00:00Z", "poll_seconds": 600}, now=NOW
    )
    assert summary["data_status"] == "stale_source"


def test_dashboard_exports_hourly_buckets(tmp_path):
    db, config = setup_empty_state(tmp_path)
    insert_analysis(db, "a", "game", "positive", "2026-11-09T11:10:00Z")
    insert_analysis(db, "b", "game", "negative", "2026-11-09T11:20:00Z")
    insert_analysis(db, "c", "game", "neutral", "2026-11-09T10:10:00Z")
    db.commit()
    payload = export_dashboard(db, config, now=NOW, days=1, bucket_hours=1)
    game = next(item for item in payload["entities"] if item["entity_id"] == "game")
    buckets = {bucket["bucket_start"]: bucket for bucket in game["buckets"]}
    assert buckets["2026-11-09T11:00:00Z"]["mention_count"] == 2
    assert buckets["2026-11-09T11:00:00Z"]["positive_count"] == 1
    assert buckets["2026-11-09T11:00:00Z"]["negative_count"] == 1
    assert buckets["2026-11-09T10:00:00Z"]["mention_count"] == 1
    assert buckets["2026-11-09T09:00:00Z"]["data_status"] == "no_data"
