from __future__ import annotations

import json
from datetime import timedelta

from fastapi.testclient import TestClient

from sentiment_analysis import settings
from sentiment_analysis import api as sentiment_api
from sentiment_analysis.api import create_app
from sentiment_analysis.settings import ALGORITHM_VERSION, LIVE_CONSUMER, analysis_db_path
from sentiment_analysis.store import bind_state, connect, init_schema, state_set
from sentiment_analysis.util import iso_utc, utc_now

from .helpers import insert_analysis


def make_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ANALYSIS_ROOT", tmp_path)
    db_path = analysis_db_path(730, LIVE_CONSUMER)
    db = connect(db_path)
    init_schema(db)
    bind_state(db, 730, "stream-1", "live")
    now = iso_utc(utc_now() - timedelta(hours=1))
    insert_analysis(db, "vader-1", "game", "negative", now, score=-0.5)
    insert_analysis(db, "vader-2", "anticheat", "negative", now, score=-0.8)
    db.execute(
        """
        INSERT INTO review_status(
            review_id,revision,usable_for_sentiment,pipeline_version,input_fingerprint,
            status,analyzed_at,updated_at,reused_from_revision
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        ("llm-1", 1, 1, "clean-en-v3", "fp-llm-1", "analyzed", now, now, None),
    )
    db.execute(
        "INSERT INTO llm_analysis VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("llm-1", 1, "game", 1, "positive", None, "good", now, "clean-en-v3",
         "sentiment-llm-v1", "entities-v1", now, "test-model", "llm-entity-v2-overall", "{}"),
    )
    db.commit()
    return db


def test_api_health_entities_pending_and_scorer(tmp_path, monkeypatch):
    db = make_db(tmp_path, monkeypatch)
    client = TestClient(create_app(LIVE_CONSUMER, collector_url=None))
    entities = client.get("/v1/sentiment/entities?app_id=730")
    assert entities.status_code == 200
    assert len(entities.json()["entities"]) == 8
    health = client.get("/v1/sentiment/health?app_id=730")
    assert health.status_code == 200
    body = health.json()
    assert body["llm_rows"] == 1
    assert body["analysis_rows"] == 2
    assert body["pending_llm_rows"] == 1
    assert body["llm_coverage"] == 0.5
    pending = client.get("/v1/sentiment/pending?app_id=730")
    assert pending.status_code == 200
    assert pending.json()["pending_llm_rows"] == 1
    db.close()


def test_summary_and_series_llm_only_vs_effective(tmp_path, monkeypatch):
    db = make_db(tmp_path, monkeypatch)
    client = TestClient(create_app(LIVE_CONSUMER, collector_url=None))
    llm = client.get("/v1/sentiment/summary?app_id=730&window_hours=24&baseline_hours=24&scorer=llm")
    assert llm.status_code == 200
    game = next(item for item in llm.json()["entities"] if item["entity_id"] == "game")
    assert game["current"]["mention_count"] == 1
    assert game["current"]["positive_count"] == 1
    effective = client.get("/v1/sentiment/summary?app_id=730&window_hours=24&baseline_hours=24&scorer=effective")
    assert effective.status_code == 200
    game_eff = next(item for item in effective.json()["entities"] if item["entity_id"] == "game")
    assert game_eff["current"]["mention_count"] == 2
    series = client.get(
        "/v1/sentiment/series?app_id=730&entity_id=game&days=1&bucket_hours=1&scorer=llm"
    )
    assert series.status_code == 200
    payload = series.json()
    assert payload["scorer"] == "llm"
    assert payload["entities"][0]["entity_id"] == "game"
    assert sum(point["mention_count"] for point in payload["entities"][0]["buckets"]) == 1
    db.close()


def test_api_validation_and_missing_db(tmp_path, monkeypatch):
    db = make_db(tmp_path, monkeypatch)
    client = TestClient(create_app(LIVE_CONSUMER, collector_url=None))
    assert client.get("/v1/sentiment/health?app_id=999").status_code == 422
    assert client.get("/v1/sentiment/health?app_id=570").status_code == 404
    assert client.get("/v1/sentiment/series?app_id=730&entity_id=unknown").status_code == 404
    db.close()


def test_dashboard_overview_returns_aligned_sentiment_snapshot(tmp_path, monkeypatch):
    as_of = sentiment_api.parse_utc("2026-11-10T12:00:00Z")
    monkeypatch.setattr(settings, "ANALYSIS_ROOT", tmp_path)
    monkeypatch.setattr(sentiment_api, "utc_now", lambda: as_of)
    db = connect(analysis_db_path(730, LIVE_CONSUMER))
    init_schema(db)
    bind_state(db, 730, "stream-1", "live")
    state_set(db, "cursor", "100")
    state_set(db, "health_json", json.dumps({
        "mode": "live",
        "app_id": 730,
        "latest_cursor": "100",
        "backfill_status": "completed",
        "last_success_at": "2026-11-10T11:59:00Z",
        "poll_seconds": 600,
        "scope_start_unix": int(as_of.timestamp()) - 30 * 86400,
        "data_ready": True,
    }))

    def add_review(review_id, created_at, label, index):
        created = iso_utc(created_at)
        record = {
            "schema_version": "1.0",
            "app_id": 730,
            "review_id": review_id,
            "revision": 1,
            "created_at": created,
            "text_raw": f"review text {index}",
            "text_clean": f"review text {index}",
            "steam_recommended": index % 2 == 0,
            "usable_for_sentiment": True,
            "pipeline_version": "clean-en-v3",
        }
        db.execute(
            "INSERT INTO reviews(review_id,revision,record_json) VALUES(?,?,?)",
            (review_id, 1, json.dumps(record)),
        )
        insert_analysis(db, review_id, "game", label, created)

    for index in range(20):
        add_review(f"recent-{index}", as_of - timedelta(minutes=20), "negative", index)
        add_review(f"baseline-{index}", as_of - timedelta(hours=12), "positive", index)
    db.commit()
    db.close()

    client = TestClient(create_app(LIVE_CONSUMER, collector_url=None))
    response = client.get("/v1/dashboard/overview?app_id=730&hours=6")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "1.0"
    assert body["app_id"] == 730
    assert body["as_of"] == "2026-11-10T12:00:00Z"
    assert len(body["buckets"]) == 6
    assert body["summary"] == {
        "review_count": 20,
        "classified_count": 20,
        "positive_count": 0,
        "neutral_count": 0,
        "negative_count": 20,
    }
    assert body["comparison"]["recent"] == {
        "start_at": "2026-11-10T11:00:00Z",
        "end_at": "2026-11-10T12:00:00Z",
    }
    assert body["comparison"]["baseline"] == {
        "start_at": "2026-11-09T11:00:00Z",
        "end_at": "2026-11-10T11:00:00Z",
    }
    assert body["status"]["data_state"] == "ready"
    assert body["alert"]["status"] == "triggered"
    assert body["alert"]["recent"] == {"classified_count": 20, "negative_count": 20}
    assert body["alert"]["baseline"] == {"classified_count": 20, "negative_count": 0}
    assert len(body["review_samples"]) == 5
    assert all(sample["sentiment"] == "negative" for sample in body["review_samples"])
    assert body["trends"]["status"] == "pending"


def test_dashboard_overview_rejects_unsupported_hours(tmp_path, monkeypatch):
    make_db(tmp_path, monkeypatch)
    client = TestClient(create_app(LIVE_CONSUMER, collector_url=None))
    response = client.get("/v1/dashboard/overview?app_id=730&hours=12")
    assert response.status_code == 422
