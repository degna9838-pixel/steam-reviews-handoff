import copy

import pytest
from fastapi.testclient import TestClient

from cs2_reviews.api import create_app
from cs2_reviews.storage import Store


def raw(review_id="1", text="CS2 is fun but the servers are not good.", updated=200):
    return {"recommendationid": review_id, "review": text, "language": "english",
            "timestamp_created": 100, "timestamp_updated": updated, "voted_up": True,
            "author": {"steamid": "not-for-public-api"}}


def test_upsert_idempotent_edit_history_and_stale_cache(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    assert store.apply_page([raw()], now="2026-11-01T00:00:00Z")["inserted"] == 1
    store.apply_page([raw()], now="2026-11-02T00:00:00Z")
    assert len(store.changes()["items"]) == 1
    assert store.review("1")["last_seen_at"] == "2026-11-02T00:00:00Z"
    edit = raw(text="The latest update fixed the server issues.", updated=300)
    assert store.apply_page([edit])["updated"] == 1
    assert store.review("1")["revision"] == 2
    assert store.changes()["items"][0]["review"]["text_raw"] == raw()["review"]
    store.apply_page([raw()])
    assert store.review("1")["text_raw"] == edit["review"]
    assert store.stats()["review_count"] == 1
    store.apply_page([raw("2", text=edit["review"], updated=300)])
    assert store.stats()["review_count"] == 2  # Same text from another ID is retained.


def test_invalid_page_does_not_advance_checkpoint_or_commit_partial_rows(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    invalid = raw("2")
    invalid.pop("timestamp_updated")
    with pytest.raises(ValueError):
        store.apply_page([raw(), invalid], state={"checkpoint_recent": "advanced"})
    assert store.stats()["review_count"] == 0
    assert store.get("checkpoint_recent") is None


def test_api_paging_stream_epoch_privacy_and_validation(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.apply_page([raw("1"), raw("2")])
    with TestClient(create_app(store)) as client:
        health = client.get("/v1/health").json()
        assert health["mode"] == "offline" and health["review_count"] == 2
        first = client.get("/v1/reviews/changes?cursor=0&limit=1").json()
        assert first["has_more"] and first["next_cursor"] == "1"
        second = client.get("/v1/reviews/changes?cursor=1&limit=1").json()
        assert not second["has_more"] and second["items"][0]["review_id"] == "2"
        assert first["stream_id"] == second["stream_id"]
        assert "not-for-public-api" not in str(first)
        assert client.get("/v1/reviews/changes?cursor=100").status_code == 409
        assert client.get("/v1/reviews/changes?cursor=-1").status_code == 422
        assert client.get("/v1/reviews?limit=1001").status_code == 422
        assert client.get("/v1/reviews?usable_for=invalid").status_code == 422
        assert client.get("/v1/reviews/1").json()["review_id"] == "1"
        assert client.get("/v1/reviews/missing").status_code == 404
        page = client.get("/v1/reviews?limit=1").json()
        assert page["has_more"] and page["next_after_id"] == 1
        assert client.get("/v1/reviews?after_id=1").json()["items"][0]["review_id"] == "2"
        assert client.get("/openapi.json").status_code == 200


def test_analysis_filter_and_export_keep_rejected_records(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.apply_page([raw("1"), raw("2", "★★★ !!!")])
    assert len(store.current(usable_for="trend")["items"]) == 1
    out = tmp_path / "out.jsonl"
    assert store.export(out) == 2
    assert len(out.read_text().splitlines()) == 2


def test_database_restart_preserves_stream_id(tmp_path):
    path = tmp_path / "db.sqlite3"
    one = Store(path)
    one.apply_page([raw()])
    two = Store(path)
    assert two.get("stream_id") == one.get("stream_id")
    assert two.stats()["review_count"] == 1
