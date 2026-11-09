"""Interaction counters: API, persistence, migration and consumer compatibility."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cs2_reviews.api import create_app
from cs2_reviews.collector import CollectorLock
from cs2_reviews.maintenance import ensure_current_cleaning
from cs2_reviews.storage import Store


def raw(review_id="1", **changes):
    return {"recommendationid": review_id, "review": "This game is fun, but its servers are not good.",
            "language": "english", "timestamp_created": 100, "timestamp_updated": 200,
            "voted_up": True, "votes_up": 3, "comment_count": 4,
            "author": {"steamid": "not-in-analysis-api"}, **changes}


def make_legacy(store):
    with store.connect() as db:
        for table, identity in (("reviews", "review_id"), ("changes", "change_id")):
            for row in db.execute(f"SELECT {identity},record_json FROM {table}").fetchall():
                record = json.loads(row["record_json"])
                record.pop("votes_up", None)
                record.pop("comment_count", None)
                db.execute(f"UPDATE {table} SET record_json=? WHERE {identity}=?", (json.dumps(record), row[identity]))
        db.execute("UPDATE reviews SET signature='pre-engagement-extension'")


@pytest.mark.parametrize("app_id", [730, 570, 4115450])
def test_api_counts_in_current_filters_single_and_changes(tmp_path, app_id):
    stores = {game: Store(tmp_path / str(game) / "reviews.sqlite3", app_id=game) for game in (730, 570, 4115450)}
    for game, store in stores.items():
        store.apply_page([raw(votes_up=game, comment_count=game + 1)])
    with TestClient(create_app(stores[730], stores=stores)) as client:
        for path, params, changes in (("/v1/reviews", {}, False),
                                     ("/v1/reviews", {"usable_for": "trend"}, False),
                                     ("/v1/reviews", {"usable_for": "sentiment"}, False),
                                     ("/v1/reviews/changes", {}, True)):
            response = client.get(path, params={"app_id": app_id, **params})
            assert response.status_code == 200
            item = response.json()["items"][0]
            review = item["review"] if changes else item
            assert review["app_id"] == app_id
            assert review["votes_up"] == app_id and review["comment_count"] == app_id + 1
            assert type(review["votes_up"]) is type(review["comment_count"]) is int
            assert "author" not in review and "not-in-analysis-api" not in response.text
        single = client.get("/v1/reviews/1", params={"app_id": app_id})
        assert single.status_code == 200
        assert (single.json()["votes_up"], single.json()["comment_count"]) == (app_id, app_id + 1)
        schema = client.get("/openapi.json").json()["components"]["schemas"]["ReviewRecord"]
        assert {"votes_up", "comment_count"} <= set(schema["properties"])


def test_missing_counts_are_unknown_not_zero_and_export_preserves_them(tmp_path):
    store = Store(tmp_path / "reviews.sqlite3")
    missing = raw()
    missing.pop("votes_up")
    missing.pop("comment_count")
    store.apply_page([missing, raw("2", votes_up=0, comment_count=0)])
    assert store.review("1")["votes_up"] is None and store.review("1")["comment_count"] is None
    assert store.review("2")["votes_up"] == 0 and store.review("2")["comment_count"] == 0
    with TestClient(create_app(store)) as client:
        assert client.get("/v1/reviews/1").json()["votes_up"] is None
        assert client.get("/v1/reviews/2").json()["votes_up"] == 0
    path = tmp_path / "reviews_current.jsonl"
    assert store.export(path) == 2
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["votes_up"] is None and rows[1]["votes_up"] == 0
    assert json.loads(path.with_suffix(".manifest.json").read_text())["snapshot_cursor"] == "2"
    assert not store.needs_reprocess()  # Stored JSON null is a valid known-unknown value.


@pytest.mark.parametrize("field", ["votes_up", "comment_count"])
@pytest.mark.parametrize("invalid", [-1, True, 1.5, "2", {}, []])
def test_invalid_counter_rejects_whole_page_and_progress(tmp_path, field, invalid):
    store = Store(tmp_path / "reviews.sqlite3")
    store.apply_page([raw()], state={"checkpoint_recent": {"cursor": "saved"}})
    before = store.review("1")
    with pytest.raises(ValueError):
        store.apply_page([raw("2"), raw("3", **{field: invalid})], state={"checkpoint_recent": "wrong"})
    assert store.review("1") == before and store.review("2") is None and store.review("3") is None
    assert store.stats()["latest_cursor"] == "1" and store.get("checkpoint_recent") == {"cursor": "saved"}


def test_counter_changes_revise_without_text_changes_and_can_decrease(tmp_path):
    store = Store(tmp_path / "reviews.sqlite3")
    store.apply_page([raw()], now="2026-11-01T00:00:00Z")
    before = store.review("1")
    assert store.apply_page([raw(votes_up=7, comment_count=8)], now="2026-11-02T00:00:00Z")["updated"] == 1
    after = store.review("1")
    assert after["revision"] == 2 and after["last_seen_at"] == "2026-11-02T00:00:00Z"
    for key in ("created_at", "updated_at", "first_seen_at", "text_raw", "text_clean", "tokens_trend", "pipeline_version"):
        assert after[key] == before[key]
    assert store.apply_page([raw(votes_up=7, comment_count=8)])["unchanged"] == 1
    assert store.stats()["latest_cursor"] == "2"
    assert store.apply_page([raw(votes_up=1, comment_count=0)])["updated"] == 1
    assert [(e["review"]["votes_up"], e["review"]["comment_count"]) for e in store.changes()["items"]] == [(3, 4), (7, 8), (1, 0)]
    assert store.stats()["review_count"] == 1


def test_stale_source_does_not_replace_newer_counts(tmp_path):
    store = Store(tmp_path / "reviews.sqlite3")
    store.apply_page([raw(timestamp_updated=300, votes_up=8, comment_count=9)])
    assert store.apply_page([raw(timestamp_updated=200, votes_up=100, comment_count=100)])["unchanged"] == 1
    row = store.review("1")
    assert (row["votes_up"], row["comment_count"], row["revision"]) == (8, 9, 1)


def test_legacy_v3_upgrade_backs_up_and_keeps_identity_times_and_progress(tmp_path):
    store = Store(tmp_path / "reviews.sqlite3")
    store.apply_page([raw()], now="2026-11-01T00:00:00Z")
    before = store.review("1")
    progress = {"scope_start_unix": 50, "checkpoint_recent": {"cursor": "saved"}, "watermark_recent": 500,
                "next_mode": "recent", "backfill_status": "running"}
    store.set_many(progress)
    stream = store.get("stream_id")
    make_legacy(store)
    assert store.pipeline_versions() == ["clean-en-v3"] and store.needs_reprocess()
    with CollectorLock(store.path.with_suffix(".lock")):
        result = ensure_current_cleaning(store)
    assert result is not None and result["updated"] == 1 and Path(result["backup"]).is_file()
    with sqlite3.connect(result["backup"]) as db:
        assert "votes_up" not in json.loads(db.execute("SELECT record_json FROM reviews").fetchone()[0])
    after = store.review("1")
    assert (after["votes_up"], after["comment_count"], after["revision"]) == (3, 4, 2)
    for key in ("review_id", "created_at", "updated_at", "first_seen_at", "last_seen_at", "pipeline_version", "text_clean", "tokens_trend"):
        assert after[key] == before[key]
    assert store.get("stream_id") == stream and all(store.get(k) == v for k, v in progress.items())
    assert not store.needs_reprocess() and ensure_current_cleaning(store) is None
    with TestClient(create_app(store)) as client:
        events = client.get("/v1/reviews/changes").json()["items"]
        assert events[0]["review"]["votes_up"] is None and events[0]["review"]["comment_count"] is None
        assert events[1]["review"]["votes_up"] == 3


@pytest.mark.parametrize("app_id", [730, 570, 4115450])
def test_consumer_counter_updates_are_idempotent(tmp_path, app_id):
    spec = importlib.util.spec_from_file_location("engagement_consumer", Path(__file__).parents[1] / "examples/consume_api.py")
    consumer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(consumer)
    store = Store(tmp_path / "reviews.sqlite3", app_id=app_id)
    store.apply_page([raw()])
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("CREATE TABLE reviews(review_id TEXT PRIMARY KEY,revision INTEGER,record_json TEXT)")
        db.execute("CREATE TABLE presentation(review_id TEXT PRIMARY KEY,votes_up INTEGER,comment_count INTEGER)")
        seen = []
        def update(db, old, new):
            seen.append(None if old is None else old["votes_up"])
            db.execute("INSERT OR REPLACE INTO presentation VALUES(?,?,?)", (new["review_id"], new["votes_up"], new["comment_count"]))
        assert consumer.save_batch(db, store.changes(), 0, app_id=app_id, on_upsert=update) == (1, 1)
        store.apply_page([raw(votes_up=12, comment_count=6)])
        batch = store.changes(1)
        assert consumer.save_batch(db, batch, 1, app_id=app_id, on_upsert=update) == (2, 1)
        assert consumer.save_batch(db, batch, 1, app_id=app_id, on_upsert=update) == (2, 0)
        assert seen == [None, 3]
        assert db.execute("SELECT votes_up,comment_count FROM presentation").fetchone() == (12, 6)
        assert db.execute("SELECT value FROM state WHERE key='cursor'").fetchone()[0] == "2"


@pytest.mark.parametrize("field", ["votes_up", "comment_count"])
@pytest.mark.parametrize("invalid", [-1, True, "2", 1.5])
def test_consumer_invalid_counter_rolls_back_record_and_cursor(tmp_path, field, invalid):
    spec = importlib.util.spec_from_file_location("invalid_counter_consumer", Path(__file__).parents[1] / "examples/consume_api.py")
    consumer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(consumer)
    store = Store(tmp_path / "reviews.sqlite3")
    store.apply_page([raw()])
    batch = store.changes()
    batch["items"][0]["review"][field] = invalid
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("CREATE TABLE reviews(review_id TEXT PRIMARY KEY,revision INTEGER,record_json TEXT)")
        with pytest.raises(ValueError):
            consumer.save_batch(db, batch, 0)
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM state").fetchone()[0] == 0
