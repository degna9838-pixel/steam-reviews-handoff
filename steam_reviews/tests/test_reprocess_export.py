import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from cs2_reviews import cleaning
from cs2_reviews.storage import Store, unix_to_iso


def raw(review_id="1", text="CS2 is fun."):
    return {"recommendationid": review_id, "review": text, "language": "english",
            "timestamp_created": 100, "timestamp_updated": 200, "voted_up": True,
            "author": {"steamid": "private-source-metadata"}}


def install_cleaner(monkeypatch, version, *, extra_for=None, fail_for=None):
    def clean(text, api_language="english"):
        if text == fail_for:
            raise ValueError("Test preprocessing failure")
        return {"text_raw": text, "text_clean": text,
                "tokens_trend": [text.lower()] + (["normalized"] if text == extra_for else []),
                "language_check": "passed", "quality_flags": [], "usable_for_trend": True,
                "usable_for_sentiment": True, "pipeline_version": version}
    monkeypatch.setattr(cleaning, "PIPELINE_VERSION", version)
    monkeypatch.setattr(cleaning, "clean_review_text", clean)


def test_reprocess_preserves_identity_source_and_observation_times(monkeypatch, tmp_path):
    install_cleaner(monkeypatch, "clean-en-v1")
    store = Store(tmp_path / "reviews.sqlite3")
    progress = {"scope_start_unix": 50, "checkpoint_recent": {"cursor": "saved"},
                "watermark_recent": 500, "next_mode": "recent", "backfill_status": "running"}
    store.set_many(progress)
    store.apply_page([raw()], now="2026-11-01T00:00:00Z")
    store.apply_page([raw()], now="2026-11-02T00:00:00Z")
    before, stream = store.review("1"), store.get("stream_id")
    with store.connect() as db:
        row_before = dict(db.execute("SELECT id,raw_json FROM reviews").fetchone())
    install_cleaner(monkeypatch, "clean-en-v2")
    assert store.needs_reprocess()
    now = "2026-11-05T12:00:00Z"
    result = store.reprocess(now=now)
    after = store.review("1")
    assert result["processed"] == result["updated"] == 1 and result["unchanged"] == 0
    assert after["revision"] == 2 and after["pipeline_version"] == "clean-en-v2"
    for key in ("created_at", "updated_at", "first_seen_at", "last_seen_at", "review_id"):
        assert after[key] == before[key]
    with store.connect() as db:
        assert dict(db.execute("SELECT id,raw_json FROM reviews").fetchone()) == row_before
        assert db.execute("SELECT observed_at FROM changes ORDER BY change_id DESC LIMIT 1").fetchone()[0] == now
    assert store.get("stream_id") == stream
    assert all(store.get(key) == value for key, value in progress.items())
    assert store.changes()["items"][0]["review"]["pipeline_version"] == "clean-en-v1"
    assert store.get("last_reprocess_pipeline_version") == "clean-en-v2"
    assert store.pipeline_versions() == ["clean-en-v2"] and not store.needs_reprocess()
    cursor = store.stats()["latest_cursor"]
    assert store.reprocess()["updated"] == 0
    assert store.stats()["latest_cursor"] == cursor
    assert store.apply_page([raw()])["unchanged"] == 1
    assert store.stats()["latest_cursor"] == cursor


def test_same_version_output_change_only_revises_changed_record(monkeypatch, tmp_path):
    install_cleaner(monkeypatch, "clean-en-v2")
    store = Store(tmp_path / "reviews.sqlite3")
    store.apply_page([raw("1", "one"), raw("2", "two")])
    install_cleaner(monkeypatch, "clean-en-v2", extra_for="one")
    result = store.reprocess()
    assert result["updated"] == 1 and result["unchanged"] == 1
    assert store.review("1")["revision"] == 2 and store.review("2")["revision"] == 1


def test_reprocess_failure_rolls_back_all_records_and_history(monkeypatch, tmp_path):
    install_cleaner(monkeypatch, "clean-en-v1")
    store = Store(tmp_path / "reviews.sqlite3")
    store.apply_page([raw("1", "one"), raw("2", "two")])
    before = store.current()["items"]
    install_cleaner(monkeypatch, "clean-en-v2", fail_for="two")
    with pytest.raises(ValueError, match="preprocessing failure"):
        store.reprocess()
    assert store.current()["items"] == before and store.stats()["latest_cursor"] == "2"
    assert store.get("last_reprocess_at") is None


def test_signature_repair_does_not_fabricate_revision(monkeypatch, tmp_path):
    install_cleaner(monkeypatch, "clean-en-v2")
    store = Store(tmp_path / "reviews.sqlite3")
    store.apply_page([raw()])
    with store.connect() as db:
        db.execute("UPDATE reviews SET signature='legacy'")
    assert store.reprocess()["updated"] == 0
    assert store.apply_page([raw()])["unchanged"] == 1
    assert store.stats()["latest_cursor"] == "1"


@pytest.mark.parametrize("sample", [False, True])
def test_export_manifest_matches_content_scope_mode_and_versions(monkeypatch, tmp_path, sample):
    install_cleaner(monkeypatch, "clean-en-v2")
    store = Store(tmp_path / ("sample.sqlite3" if sample else "reviews.sqlite3"))
    store.set_many({"scope_start_unix": 50, "backfill_status": "completed"})
    store.apply_page([raw("1", "English text with emoji 😊"), raw("2", "other")])
    target = tmp_path / "reviews_current.jsonl"
    assert store.export(target) == 2
    manifest = json.loads(target.with_suffix(".manifest.json").read_text())
    assert manifest["jsonl_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert manifest["row_count"] == len(target.read_text().splitlines()) == 2
    assert manifest["pipeline_versions"] == ["clean-en-v2"]
    assert manifest["scope_start_at"] == unix_to_iso(50)
    assert manifest["backfill_status"] == "completed" and manifest["snapshot_cursor"] == "2"
    assert manifest["stream_id"] == store.get("stream_id")
    assert manifest["mode"] == ("sample" if sample else "live")
    assert manifest["jsonl_file"] == target.name and manifest["exported_at"].endswith("Z")


def test_export_rows_head_and_metadata_share_read_snapshot(monkeypatch, tmp_path):
    install_cleaner(monkeypatch, "clean-en-v2")
    path = tmp_path / "reviews.sqlite3"
    store, writer = Store(path), Store(path)
    store.set("scope_start_unix", 50)
    store.apply_page([raw("1")])
    original_connect, inserted = store.connect, False

    class InterleavingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args):
            nonlocal inserted
            cursor = self.connection.execute(sql, *args)
            if "COALESCE(MAX(change_id)" in sql and not inserted:
                inserted = True
                writer.apply_page([raw("2")])
                writer.set("scope_start_unix", 10)
            return cursor

    @contextmanager
    def interleaving_connect():
        with original_connect() as connection:
            yield InterleavingConnection(connection)

    monkeypatch.setattr(store, "connect", interleaving_connect)
    target = tmp_path / "snapshot.jsonl"
    assert store.export(target) == 1
    manifest = json.loads(target.with_suffix(".manifest.json").read_text())
    assert inserted and writer.stats()["review_count"] == 2
    assert manifest["row_count"] == 1 and manifest["snapshot_cursor"] == "1"
    assert manifest["scope_start_at"] == unix_to_iso(50)


def test_interrupted_pair_publication_is_detectable(monkeypatch, tmp_path):
    install_cleaner(monkeypatch, "clean-en-v2")
    store = Store(tmp_path / "reviews.sqlite3")
    store.apply_page([raw("1")])
    target = tmp_path / "reviews_current.jsonl"
    manifest_path = target.with_suffix(".manifest.json")
    store.export(target)
    old_manifest = manifest_path.read_bytes()
    store.apply_page([raw("2")])
    original_replace = Path.replace

    def fail_manifest_replace(source, destination):
        if Path(destination) == manifest_path:
            raise OSError("Simulated interruption")
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_manifest_replace)
    with pytest.raises(OSError, match="Simulated interruption"):
        store.export(target)
    assert manifest_path.read_bytes() == old_manifest
    assert json.loads(old_manifest)["jsonl_sha256"] != hashlib.sha256(target.read_bytes()).hexdigest()
    assert not list(tmp_path.glob(".*.tmp"))


def test_empty_database_manifest(monkeypatch, tmp_path):
    install_cleaner(monkeypatch, "clean-en-v2")
    store = Store(tmp_path / "reviews.sqlite3")
    assert not store.needs_reprocess() and store.pipeline_versions() == []
    assert store.reprocess()["processed"] == 0
    target = tmp_path / "empty.jsonl"
    assert store.export(target) == 0 and target.read_bytes() == b""
    manifest = json.loads(target.with_suffix(".manifest.json").read_text())
    assert manifest["snapshot_cursor"] == "0" and manifest["pipeline_versions"] == []
