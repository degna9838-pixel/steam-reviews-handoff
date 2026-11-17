"""Contract and cross-module regressions for the combined dashboard route."""
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from sentiment_analysis import api, settings
from sentiment_analysis.store import bind_state, connect, init_schema, state_set
from sentiment_analysis.util import iso_utc, parse_utc

from .helpers import insert_analysis, record

AS_OF = parse_utc("2026-11-17T12:00:00Z")


def snapshot(tmp_path, monkeypatch, *, app_id=730, mode="live"):
    monkeypatch.setattr(settings, "ANALYSIS_ROOT", tmp_path)
    monkeypatch.setattr(api, "utc_now", lambda: AS_OF + timedelta(minutes=17))
    db = connect(settings.analysis_db_path(app_id, "overview_demo"))
    init_schema(db)
    bind_state(db, app_id, "stream-1", mode)
    state_set(db, "cursor", "100")
    health = {"app_id": app_id, "stream_id": "stream-1", "mode": mode,
              "latest_cursor": "100", "data_ready": True, "cleaning_ready": True,
              "backfill_status": "completed", "poll_seconds": 600,
              "scope_start_unix": int((AS_OF - timedelta(days=30)).timestamp()),
              "last_success_at": iso_utc(AS_OF + timedelta(minutes=16))}
    state_set(db, "health_json", json.dumps(health))

    def add(review_id, created, label, tokens, *, usable=True, revision=1, analyzed=True):
        item = record(str(review_id), app_id=app_id, created_at=iso_utc(created), revision=revision)
        item.update(tokens_trend=tokens, usable_for_trend=usable)
        db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?)", (str(review_id), revision, json.dumps(item)))
        if analyzed:
            insert_analysis(db, str(review_id), "game", label, iso_utc(created), revision=revision)

    for index in range(20):
        add(1000 + index, AS_OF - timedelta(minutes=30), "negative", ["lag", "lag"])
    for index in range(30):
        add(2000 + index, AS_OF - timedelta(hours=12), "positive", ["smooth"])
    add(3000, AS_OF - timedelta(days=5), "positive", ["old"])
    db.commit()
    return db, health, add, TestClient(api.create_app("overview_demo", collector_url=None))


def overview(client, *, app_id=730, hours=24):
    response = client.get(f"/v1/dashboard/overview?app_id={app_id}&hours={hours}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("app_id", [730, 570, 4115450])
@pytest.mark.parametrize("hours,expected", [(6, 20), (24, 50), (168, 51)])
def test_combines_all_modules_at_one_cutoff(tmp_path, monkeypatch, app_id, hours, expected):
    db, _, _, client = snapshot(tmp_path, monkeypatch, app_id=app_id)
    try:
        body = overview(client, app_id=app_id, hours=hours)
        assert body["schema_version"] == "1.0"
        assert body["as_of"] == iso_utc(AS_OF)
        assert body["status"]["data_state"] == "ready"
        assert len(body["buckets"]) == hours
        assert body["summary"]["review_count"] == expected
        assert body["keywords"]["status"] == "ready"
        assert body["keywords"]["eligible_count"] == expected
        keyword_counts = {row["term"]: row["review_count"] for row in body["keywords"]["items"]}
        assert keyword_counts["lag"] == 20
        assert ("old" in keyword_counts) == (hours == 168)
        for key, total in body["summary"].items():
            assert total == sum(bucket["counts"][key] for bucket in body["buckets"])
        assert body["comparison"]["recent"]["end_at"] == body["as_of"]
        assert body["comparison"]["baseline"]["end_at"] == body["comparison"]["recent"]["start_at"]
        assert body["trends"] == {
            "status": "ready", "recent_eligible_count": 20, "baseline_eligible_count": 30,
            "items": [{"term": "lag", "recent_review_count": 20, "baseline_review_count": 0}],
        }
        assert body["alert"]["status"] == "triggered"
        assert body["alert"]["recent"] == {"classified_count": 20, "negative_count": 20}
        assert len(body["review_samples"]) == 5
        assert all(item["app_id"] == app_id for item in body["review_samples"])
    finally:
        db.close()


def test_comparison_boundaries_and_current_revision(tmp_path, monkeypatch):
    db, _, add, client = snapshot(tmp_path, monkeypatch)
    try:
        # Replace a current record; repeated lag tokens count once, withdrawn records count zero.
        add(1000, AS_OF - timedelta(minutes=30), "negative", ["lag"], usable=False, revision=2)
        add(4000, AS_OF, "negative", ["future"])
        add(4001, AS_OF - timedelta(hours=1), "positive", ["boundary"])
        add(4002, AS_OF - timedelta(hours=25), "positive", ["baseline-edge"])
        db.commit()
        body = overview(client)
        trends = body["trends"]
        assert trends["recent_eligible_count"] == 20
        assert trends["baseline_eligible_count"] == 31
        assert trends["items"][0]["recent_review_count"] == 19
        assert all(item["term"] != "future" for item in trends["items"])
        assert body["summary"]["review_count"] == 51
    finally:
        db.close()


@pytest.mark.parametrize("change", ["lag", "rewound_cursor", "stream", "missing_cursor", "scope", "stale", "cleaning"])
def test_unready_source_does_not_publish_trends(tmp_path, monkeypatch, change):
    db, health, _, client = snapshot(tmp_path, monkeypatch)
    try:
        if change == "lag": health["latest_cursor"] = "101"
        if change == "rewound_cursor": health["latest_cursor"] = "99"
        if change == "stream": health["stream_id"] = "different-stream"
        if change == "missing_cursor": health.pop("latest_cursor")
        if change == "scope": health["scope_start_unix"] = int((AS_OF - timedelta(hours=2)).timestamp())
        if change == "stale": health["last_success_at"] = iso_utc(AS_OF - timedelta(days=1))
        if change == "cleaning": health["cleaning_ready"] = False
        state_set(db, "health_json", json.dumps(health))
        db.commit()
        body = overview(client)
        assert body["status"]["data_state"] == "partial"
        assert body["trends"]["status"] == "pending"
        assert body["trends"]["items"] == []
        assert body["trends"]["recent_eligible_count"] is None
        assert body["alert"]["status"] == "pending"
        if change == "lag":
            assert body["status"]["sync_in_progress"] is True
            assert body["status"]["cursor_lag"] == 1
            assert body["summary"]["review_count"] == 50
            assert body["summary"]["classified_count"] == 50
            assert all(bucket["counts"] is not None for bucket in body["buckets"])
            assert body["keywords"]["status"] == "ready"
            assert body["keywords"]["items"]
        if change in {"rewound_cursor", "stream", "missing_cursor", "scope"}:
            assert body["summary"] is None
    finally:
        db.close()


def test_sync_completion_restores_detection_without_changing_existing_counts(tmp_path, monkeypatch):
    db, health, _, client = snapshot(tmp_path, monkeypatch)
    try:
        health["latest_cursor"] = "101"
        state_set(db, "health_json", json.dumps(health))
        db.commit()
        syncing = overview(client)
        assert syncing["status"]["data_state"] == "partial"
        assert syncing["alert"]["status"] == "pending"
        state_set(db, "cursor", "101")
        db.commit()
        synced = overview(client)
        assert synced["status"]["data_state"] == "ready"
        assert synced["status"]["sync_in_progress"] is False
        assert synced["summary"] == syncing["summary"]
        assert synced["buckets"] == syncing["buckets"]
        assert synced["trends"]["status"] == "ready"
        assert synced["alert"]["status"] == "triggered"
    finally:
        db.close()


def test_missing_coverage_is_unknown_instead_of_zero(tmp_path, monkeypatch):
    db, health, _, client = snapshot(tmp_path, monkeypatch)
    try:
        health.pop("scope_start_unix")
        state_set(db, "health_json", json.dumps(health))
        db.commit()
        body = overview(client)
        assert body["summary"] is None
        assert all(bucket["counts"] is None for bucket in body["buckets"])
        assert body["trends"]["status"] == "pending"
    finally:
        db.close()


def test_demo_sample_is_labelled_and_reports_insufficient_data(tmp_path, monkeypatch):
    db, health, _, client = snapshot(tmp_path, monkeypatch, mode="sample")
    try:
        health.pop("last_success_at")
        health.pop("backfill_status")
        health.pop("scope_start_unix")
        health["mode"] = "offline"
        state_set(db, "health_json", json.dumps(health))
        db.execute("DELETE FROM reviews WHERE review_id NOT IN ('1000','2000')")
        db.commit()
        body = overview(client)
        assert body["status"]["mode"] == "demo"
        assert body["status"]["last_collection_at"] is None
        assert body["trends"] == {"status": "insufficient_data", "recent_eligible_count": 1,
                                  "baseline_eligible_count": 1, "items": []}
        assert body["alert"]["status"] == "insufficient_data"
    finally:
        db.close()


def test_empty_ready_result_is_distinct_from_pending(tmp_path, monkeypatch):
    db, _, add, client = snapshot(tmp_path, monkeypatch)
    try:
        for index in range(20):
            add(1000 + index, AS_OF - timedelta(minutes=30), "negative", ["smooth"])
        db.commit()
        body = overview(client)
        assert body["trends"]["status"] == "ready"
        assert body["trends"]["items"] == []
        assert body["trend_candidates"][0]["term"] == "smooth"
        assert body["keywords"]["items"]
    finally:
        db.close()


def test_sufficient_demo_data_stays_explicitly_demo(tmp_path, monkeypatch):
    db, _, _, client = snapshot(tmp_path, monkeypatch, mode="sample")
    try:
        body = overview(client)
        assert body["status"]["mode"] == "demo"
        assert body["trends"]["status"] == "ready"
        assert body["alert"]["status"] == "triggered"
    finally:
        db.close()


def test_baseline_classification_lag_does_not_hide_available_trend_counts(tmp_path, monkeypatch):
    db, _, add, client = snapshot(tmp_path, monkeypatch)
    try:
        add(9999, AS_OF - timedelta(hours=12), "negative", ["smooth"], analyzed=False)
        db.commit()
        body = overview(client, hours=6)
        assert body["alert"]["status"] == "pending"
        assert body["trends"]["status"] == "ready"
    finally:
        db.close()


def test_missing_legacy_trend_flags_are_unknown(tmp_path, monkeypatch):
    db, _, _, client = snapshot(tmp_path, monkeypatch)
    try:
        item = json.loads(db.execute("SELECT record_json FROM reviews WHERE review_id='1000'").fetchone()[0])
        item.pop("usable_for_trend")
        db.execute("UPDATE reviews SET record_json=? WHERE review_id='1000'", (json.dumps(item),))
        db.commit()
        assert overview(client)["trends"]["status"] == "pending"
    finally:
        db.close()


def test_invalid_usable_tokens_fail_explicitly(tmp_path, monkeypatch):
    db, _, add, client = snapshot(tmp_path, monkeypatch)
    try:
        add(1000, AS_OF - timedelta(minutes=30), "negative", "invalid")
        db.commit()
        response = client.get("/v1/dashboard/overview?app_id=730")
        assert response.status_code == 503
        assert "Invalid trend input" in response.json()["detail"]
    finally:
        db.close()


def test_wrong_game_in_health_fails_explicitly(tmp_path, monkeypatch):
    db, health, _, client = snapshot(tmp_path, monkeypatch)
    try:
        health["app_id"] = 570
        state_set(db, "health_json", json.dumps(health))
        db.commit()
        response = client.get("/v1/dashboard/overview?app_id=730")
        assert response.status_code == 503
        assert "app_id mismatch" in response.json()["detail"]
    finally:
        db.close()
