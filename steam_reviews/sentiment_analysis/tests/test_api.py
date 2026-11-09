from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from sentiment_analysis import settings
from sentiment_analysis.api import create_app
from sentiment_analysis.settings import ALGORITHM_VERSION, LIVE_CONSUMER, analysis_db_path
from sentiment_analysis.store import bind_state, connect, init_schema
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
