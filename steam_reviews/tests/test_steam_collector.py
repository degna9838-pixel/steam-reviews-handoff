import json

import httpx
import pytest

from cs2_reviews.collector import Collector, CollectorBusy, CollectorLock
from cs2_reviews.config import Settings
from cs2_reviews.steam import SteamClient, SteamError
from cs2_reviews.storage import Store


def review(review_id="1", created=99000, updated=99000):
    return {"recommendationid": review_id, "review": "The game is fun but not perfect.",
            "timestamp_created": created, "timestamp_updated": updated,
            "language": "english", "voted_up": True}


def test_new_api_payload_and_429_retry():
    seen, sleeps = [], []
    def respond(request):
        seen.append(json.loads(request.url.params["input_json"]))
        if len(seen) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"response": {"reviews": [], "cursor": "end"}})
    client = SteamClient(Settings(), transport=httpx.MockTransport(respond), sleep=sleeps.append)
    assert client.fetch(mode="recent", cursor="a+/=", start=1, end=2)["reviews"] == []
    client.close()
    assert sleeps and len(seen) == 2
    assert seen[-1]["cursor"] == "a+/="
    assert seen[-1]["languages"] == ["english"]
    assert seen[-1]["filter_offtopic_activity"] is False
    assert seen[-1]["purchase_type"] == 1 and seen[-1]["filter"] == 1


def test_real_terminal_page_omits_reviews_but_explicitly_reports_zero():
    payload = {"response": {"query_summary": {"num_reviews": 0}, "cursor": "same", "total_matching": 3626}}
    client = SteamClient(Settings(max_retries=0), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    assert client.fetch(mode="recent", cursor="same", start=1, end=2)["reviews"] == []
    client.close()


@pytest.mark.parametrize("payload", [{}, {"response": {}}, {"response": {"reviews": "invalid"}}, {"response": {"reviews": [{}]}}])
def test_bad_schema_is_not_treated_as_empty_page(payload):
    client = SteamClient(Settings(max_retries=0), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    with pytest.raises(SteamError):
        client.fetch(mode="recent", cursor="*", start=1, end=2)
    client.close()


class FakeSteam:
    def __init__(self, actions):
        self.actions, self.calls = list(actions), []
    def fetch(self, **kwargs):
        self.calls.append(kwargs)
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action
    def close(self):
        pass


def test_one_page_budget_rotates_to_updated_without_false_completion(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    fake = FakeSteam([{"reviews": []}, {"reviews": []}])
    collector = Collector(store, Settings(max_pages_per_cycle=1), fake, clock=lambda: 100000)
    one = collector.collect_once()
    assert one["sweep_complete"] is False and store.get("next_mode") == "updated"
    two = collector.collect_once()
    assert two["sweep_complete"] is True
    assert [call["mode"] for call in fake.calls] == ["recent", "updated"]


def test_failure_resumes_exact_cursor_then_captures_edited_review(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    fake = FakeSteam([
        {"reviews": [review()], "cursor": "page2"}, SteamError("network interrupted"),
        {"reviews": []}, {"reviews": [review(updated=99999)], "cursor": "u2"}, {"reviews": []},
    ])
    collector = Collector(store, Settings(request_delay_seconds=0.001), fake, clock=lambda: 100000)
    with pytest.raises(SteamError):
        collector.collect_once()
    assert store.get("checkpoint_recent")["cursor"] == "page2"
    assert store.stats()["review_count"] == 1
    result = collector.collect_once()
    assert fake.calls[2]["cursor"] == "page2"
    assert result["sweep_complete"]
    assert store.review("1")["revision"] == 2
    assert store.get("last_error") is None


def test_stuck_cursor_does_not_commit_page(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    fake = FakeSteam([{"reviews": [review()], "cursor": "*"}])
    with pytest.raises(RuntimeError, match="did not advance"):
        Collector(store, Settings(), fake, clock=lambda: 100000).collect_once()
    assert store.stats()["review_count"] == 0
    assert store.get("checkpoint_recent")["cursor"] == "*"


def test_updated_uses_creation_scope_and_update_watermark(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.set("scope_start_unix", 50000)
    store.set("next_mode", "updated")
    store.set("watermark_updated", 90000)
    fake = FakeSteam([{"reviews": [review("1", created=60000, updated=99900), review("2", created=70000, updated=80000)], "cursor": "x"}])
    Collector(store, Settings(), fake, clock=lambda: 100000).collect_once()
    assert fake.calls[0]["start"] == 50000
    assert store.review("1") is not None and store.review("2") is None


def test_collector_process_lock(tmp_path):
    path = tmp_path / "collector.lock"
    with CollectorLock(path):
        with pytest.raises(CollectorBusy):
            with CollectorLock(path):
                pass
    with CollectorLock(path):
        pass


def test_daily_reconciliation_revisits_scope_after_overlap(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.set("scope_start_unix", 50000)
    store.set("watermark_recent", 190000)
    store.set("watermark_updated", 190000)
    store.set("last_reconcile_unix", 100000)
    fake = FakeSteam([{"reviews": []}, {"reviews": []}])
    result = Collector(store, Settings(), fake, clock=lambda: 200000).collect_once()
    assert result["sweep_complete"]
    assert all(call["start"] == 50000 for call in fake.calls)
    assert store.get("last_reconcile_unix") == 200000


@pytest.mark.parametrize("value", [[], None, {"request_delay_seconds": float("nan")}, {"port": True}])
def test_invalid_config_is_rejected(tmp_path, value):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        Settings.load(path)
