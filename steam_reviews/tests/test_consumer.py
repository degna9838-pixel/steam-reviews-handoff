import importlib.util
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("consumer", Path(__file__).parents[1] / "examples/consume_api.py")
consumer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(consumer)


def connection():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    db.execute("CREATE TABLE reviews(review_id TEXT PRIMARY KEY,revision INTEGER,record_json TEXT)")
    return db


def batch(revision=1, change_id=1, stream="one", app_id=730):
    return {"schema_version": "1.0", "app_id": app_id, "stream_id": stream, "items": [{"change_id": change_id,
            "operation": "upsert", "review_id": "1", "revision": revision,
            "review": {"schema_version": "1.0", "app_id": app_id, "pipeline_version": "clean-en-v2", "review_id": "1", "revision": revision}}], "next_cursor": str(change_id), "has_more": False}


def test_unknown_cleaning_version_stops_without_advancing():
    db = connection()
    invalid = batch()
    invalid["items"][0]["review"]["pipeline_version"] = "future-v99"
    with pytest.raises(ValueError, match="Unsupported"):
        consumer.save_batch(db, invalid, 0)
    assert db.execute("SELECT COUNT(*) FROM state").fetchone()[0] == 0


def test_analysis_callback_receives_old_and_retracts_in_same_transaction():
    db = connection()
    db.execute("CREATE TABLE analysis(review_id TEXT PRIMARY KEY, usable INTEGER)")
    def update(db, old, new):
        db.execute("INSERT OR REPLACE INTO analysis VALUES(?,?)", (new["review_id"], int(new.get("usable_for_trend", False))))
        if new["revision"] == 2:
            assert old["revision"] == 1
    first = batch()
    first["items"][0]["review"]["usable_for_trend"] = True
    consumer.save_batch(db, first, 0, on_upsert=update)
    consumer.save_batch(db, batch(2, 2), 1, on_upsert=update)
    assert db.execute("SELECT usable FROM analysis").fetchone()[0] == 0
    def fail(db, old, new):
        db.execute("DELETE FROM analysis")
        raise ValueError("analysis failed")
    with pytest.raises(ValueError, match="analysis failed"):
        consumer.save_batch(db, batch(3, 3), 2, on_upsert=fail)
    assert db.execute("SELECT COUNT(*) FROM analysis").fetchone()[0] == 1
    assert db.execute("SELECT value FROM state WHERE key='cursor'").fetchone()[0] == "2"


def test_snapshot_bootstrap_and_following_update(tmp_path):
    import hashlib
    import json
    path = tmp_path / "reviews.jsonl"
    record = batch()["items"][0]["review"]
    payload = (json.dumps(record) + "\n").encode()
    path.write_bytes(payload)
    manifest = {"jsonl_file": path.name, "jsonl_sha256": hashlib.sha256(payload).hexdigest(),
                "schema_version": "1.0", "stream_id": "one", "mode": "live",
                "pipeline_versions": ["clean-en-v2"], "row_count": 1, "snapshot_cursor": "10"}
    path.with_suffix(".manifest.json").write_text(json.dumps(manifest))
    db = connection()
    assert consumer.load_snapshot(db, path) == (10, 1)
    assert consumer.save_batch(db, batch(2, 11), 10) == (11, 1)
    with pytest.raises(ValueError, match="empty consumer"):
        consumer.load_snapshot(db, path)
    path.write_bytes(payload + b"\n")
    empty = connection()
    with pytest.raises(ValueError, match="mismatch"):
        consumer.load_snapshot(empty, path)
    assert empty.execute("SELECT COUNT(*) FROM state").fetchone()[0] == 0


def test_consumer_updates_and_independent_stream_identity():
    db = connection()
    assert consumer.save_batch(db, batch(), 0) == (1, 1)
    assert consumer.save_batch(db, batch(), 0) == (1, 0)  # Replay is idempotent.
    assert consumer.save_batch(db, batch(2, 2), 1) == (2, 1)
    with pytest.raises(ValueError, match="Stream changed"):
        consumer.save_batch(db, batch(3, 3, "other"), 2)
    assert db.execute("SELECT value FROM state WHERE key='cursor'").fetchone()[0] == "2"
    db.close()


def test_consumer_failed_batch_rolls_back_reviews_and_cursor():
    db = connection()
    broken = batch()
    broken["items"].append({**broken["items"][0], "change_id": 1})
    with pytest.raises(ValueError):
        consumer.save_batch(db, broken, 0)
    assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM state").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("pipeline", ["clean-en-v1", "clean-en-v2", "clean-en-v3"])
def test_known_pipeline_history_remains_replayable(pipeline):
    db = connection()
    event = batch()
    event["items"][0]["review"]["pipeline_version"] = pipeline
    assert consumer.save_batch(db, event, 0) == (1, 1)


def test_same_consumer_name_has_separate_paths_and_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(consumer, "ROOT", tmp_path)
    assert consumer.consumer_path("trend") == tmp_path / "data/consumer_trend.sqlite3"
    for app_id, cursor in ((730, 3), (570, 7), (4115450, 11)):
        path = consumer.consumer_path("trend", app_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("CREATE TABLE reviews(review_id TEXT PRIMARY KEY,revision INTEGER,record_json TEXT)")
            consumer.save_batch(db, batch(change_id=cursor, app_id=app_id), 0, app_id=app_id)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT value FROM state WHERE key='cursor'").fetchone()[0] == str(cursor)
            assert db.execute("SELECT value FROM state WHERE key='app_id'").fetchone()[0] == str(app_id)


def test_wrong_game_rolls_back_records_callback_and_cursor():
    db = connection()
    db.execute("CREATE TABLE analysis(review_id TEXT PRIMARY KEY)")
    consumer.save_batch(db, batch(app_id=570), 0, app_id=570)
    incoming = batch(2, 2, app_id=570)
    incoming["items"].append(batch(3, 3, app_id=730)["items"][0])
    incoming["next_cursor"] = "3"
    def update(db, old, new):
        db.execute("INSERT INTO analysis VALUES(?)", (new["review_id"],))
    with pytest.raises(ValueError, match="app_id mismatch"):
        consumer.save_batch(db, incoming, 1, app_id=570, on_upsert=update)
    assert db.execute("SELECT revision FROM reviews").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM analysis").fetchone()[0] == 0
    assert db.execute("SELECT value FROM state WHERE key='cursor'").fetchone()[0] == "1"


def test_empty_batch_cannot_rebind_legacy_consumer():
    db = connection()
    consumer.save_batch(db, batch(), 0)
    db.execute("DELETE FROM state WHERE key='app_id'")
    db.commit()
    empty = {"schema_version": "1.0", "stream_id": "one", "items": [], "next_cursor": "1", "has_more": False}
    with pytest.raises(ValueError, match="app_id mismatch"):
        consumer.save_batch(db, empty, 1, app_id=570)
    assert consumer.save_batch(db, empty, 1) == (1, 0)
    assert db.execute("SELECT value FROM state WHERE key='app_id'").fetchone()[0] == "730"


@pytest.mark.parametrize("app_id", [True, 0, -1, "730"])
def test_invalid_game_selection_cannot_advance(app_id):
    db = connection()
    with pytest.raises(ValueError, match="positive integer"):
        consumer.save_batch(db, batch(), 0, app_id=app_id)
    assert db.execute("SELECT COUNT(*) FROM state").fetchone()[0] == 0


def game_snapshot(tmp_path, records, manifest_app_id=570):
    import hashlib, json
    path = tmp_path / "reviews.jsonl"
    payload = "".join(json.dumps(record) + "\n" for record in records).encode()
    path.write_bytes(payload)
    manifest = {"schema_version": "1.0", "stream_id": "one", "mode": "live", "jsonl_file": path.name,
                "jsonl_sha256": hashlib.sha256(payload).hexdigest(), "row_count": len(records),
                "pipeline_versions": sorted({r["pipeline_version"] for r in records}), "snapshot_cursor": "10"}
    if manifest_app_id is not None:
        manifest["app_id"] = manifest_app_id
    path.with_suffix(".manifest.json").write_text(json.dumps(manifest))
    return path


def test_game_snapshot_and_following_update(tmp_path):
    db = connection()
    path = game_snapshot(tmp_path, [batch(app_id=570)["items"][0]["review"]])
    assert consumer.load_snapshot(db, path, app_id=570) == (10, 1)
    assert consumer.save_batch(db, batch(2, 11, app_id=570), 10, app_id=570) == (11, 1)
    empty = connection()
    with pytest.raises(ValueError, match="manifest app_id mismatch"):
        consumer.load_snapshot(empty, path)
    assert empty.execute("SELECT COUNT(*) FROM state").fetchone()[0] == 0


def test_mixed_game_snapshot_is_atomic(tmp_path):
    first, wrong = batch(app_id=570)["items"][0]["review"], batch()["items"][0]["review"]
    wrong["review_id"] = "2"
    path = game_snapshot(tmp_path, [first, wrong])
    db = connection()
    with pytest.raises(ValueError, match="app_id mismatch"):
        consumer.load_snapshot(db, path, app_id=570)
    assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM state").fetchone()[0] == 0


def test_legacy_snapshot_game_is_checked_even_without_manifest_binding(tmp_path):
    path = game_snapshot(tmp_path, [batch()["items"][0]["review"]], manifest_app_id=None)
    assert consumer.load_snapshot(connection(), path) == (10, 1)
    with pytest.raises(ValueError, match="app_id mismatch"):
        consumer.load_snapshot(connection(), path, app_id=570)
    empty = game_snapshot(tmp_path, [], manifest_app_id=None)
    with pytest.raises(ValueError, match="Legacy empty snapshot"):
        consumer.load_snapshot(connection(), empty, app_id=570)
