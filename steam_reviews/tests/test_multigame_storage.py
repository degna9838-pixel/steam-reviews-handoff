import json

import httpx
import pytest

from cs2_reviews import config
from cs2_reviews.config import GAMES, Settings, db_path, get_game
from cs2_reviews.schemas import ReviewRecord
from cs2_reviews.steam import SteamClient
from cs2_reviews.storage import Store


def raw(text="This game is fun but not perfect."):
    return {"recommendationid": "123", "review": text, "language": "english",
            "timestamp_created": 100, "timestamp_updated": 200, "voted_up": True}


def test_catalog_settings_and_legacy_paths(monkeypatch, tmp_path):
    assert list(GAMES) == [730, 570, 4115450]
    monkeypatch.setattr(config, "ROOT", tmp_path)
    assert db_path() == tmp_path / "data/reviews.sqlite3"
    assert db_path(True) == tmp_path / "data/sample.sqlite3"
    for app_id in (570, 4115450):
        assert db_path(app_id=app_id) == tmp_path / "data/games" / str(app_id) / "reviews.sqlite3"
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"app_ids": [570, 730]}))
    assert Settings.load(path).app_ids == (570, 730)


@pytest.mark.parametrize("value", [[], None, 730, [True], [0], [-1], [730.0], ["730"], [999], [730, 730]])
def test_invalid_app_lists_rejected(tmp_path, value):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"app_ids": value}))
    with pytest.raises(ValueError):
        Settings.load(path)


@pytest.mark.parametrize("app_id", [True, 0, -1, 730.0, "730", 999])
def test_invalid_game_identity_rejected_before_writing(tmp_path, app_id):
    with pytest.raises(ValueError):
        get_game(app_id)
    path = tmp_path / "absent.sqlite3"
    with pytest.raises(ValueError):
        Store(path, app_id=app_id)
    assert not path.exists()


def test_three_games_have_independent_streams_and_progress(tmp_path):
    streams = set()
    for app_id in GAMES:
        store = Store(tmp_path / str(app_id) / "reviews.sqlite3", app_id=app_id)
        store.apply_page([raw()], state={"checkpoint_recent": {"cursor": str(app_id)}})
        assert store.app_id == store.get("app_id") == app_id
        assert store.review("123")["app_id"] == app_id
        assert store.changes()["items"][0]["review"]["app_id"] == app_id
        assert store.reprocess()["updated"] == 0
        streams.add(store.get("stream_id"))
        target = tmp_path / f"reviews_{app_id}.jsonl"
        store.export(target)
        manifest = json.loads(target.with_suffix(".manifest.json").read_text())
        assert manifest["app_id"] == app_id and manifest["snapshot_cursor"] == "1"
        ReviewRecord.model_validate(store.review("123"))
    assert len(streams) == 3


def test_legacy_cs2_keeps_stream_rows_and_checkpoint(tmp_path):
    path = tmp_path / "reviews.sqlite3"
    store = Store(path)
    store.apply_page([raw()], state={"checkpoint_recent": {"cursor": "legacy"}, "scope_start_unix": 10})
    stream, before = store.get("stream_id"), store.current()["items"]
    with store.connect() as db:
        db.execute("DELETE FROM metadata WHERE key='app_id'")
    reopened = Store(path)
    assert reopened.get("app_id") == 730 and reopened.get("stream_id") == stream
    assert reopened.current()["items"] == before
    assert reopened.get("checkpoint_recent") == {"cursor": "legacy"}
    assert reopened.stats()["latest_cursor"] == "1"


@pytest.mark.parametrize("legacy", [False, True])
def test_wrong_game_db_rejected_without_mutation(tmp_path, legacy):
    path = tmp_path / "reviews.sqlite3"
    store = Store(path)
    store.apply_page([raw()])
    if legacy:
        with store.connect() as db:
            db.execute("DELETE FROM metadata WHERE key='app_id'")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="app_id"):
        Store(path, app_id=570)
    assert path.read_bytes() == before


def test_wrong_legacy_history_rejected_before_binding(tmp_path):
    path = tmp_path / "reviews.sqlite3"
    store = Store(path)
    store.apply_page([raw()])
    with store.connect() as db:
        db.execute("DELETE FROM metadata WHERE key='app_id'")
        record = json.loads(db.execute("SELECT record_json FROM changes").fetchone()[0])
        record["app_id"] = 570
        db.execute("UPDATE changes SET record_json=?", (json.dumps(record),))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Legacy changes"):
        Store(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("app_id", [730, 570, 4115450])
def test_request_and_retry_and_redirect_share_gate(app_id):
    timeline, payloads = [], []
    def respond(request):
        timeline.append("http")
        if len(payloads) == 0:
            payloads.append(json.loads(request.url.params["input_json"]))
            return httpx.Response(429)
        if len(payloads) == 1:
            payloads.append(json.loads(request.url.params["input_json"]))
            return httpx.Response(302, headers={"Location": "/terminal"})
        return httpx.Response(200, json={"response": {"reviews": []}})
    client = SteamClient(Settings(max_retries=1), app_id=app_id, request_gate=lambda: timeline.append("gate"),
                         transport=httpx.MockTransport(respond), sleep=lambda _: None)
    try:
        assert client.fetch(mode="recent", cursor="*", start=1, end=2)["reviews"] == []
    finally:
        client.close()
    assert timeline == ["gate", "http"] * 3
    assert all(p["appid"] == app_id for p in payloads)
