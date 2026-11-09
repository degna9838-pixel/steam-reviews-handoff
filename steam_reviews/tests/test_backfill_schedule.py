import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from cs2_reviews.collector import Collector, cycle_delay
from cs2_reviews.config import Settings
from cs2_reviews.storage import Store


def source(review_id="1"):
    return {"recommendationid": review_id, "review": "CS2 is fun but not perfect.", "language": "english",
            "timestamp_created": 1900000, "timestamp_updated": 1900000, "voted_up": True}


def test_expanding_seven_to_thirty_preserves_data_and_stream_and_backup(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.apply_page([source()])
    old_start = 3000000 - 7 * 86400
    store.set_many({"scope_start_unix": old_start, "checkpoint_recent": {"cursor": "old"},
                    "checkpoint_updated": {"cursor": "old"}, "watermark_recent": 2999999,
                    "watermark_updated": 2999999, "next_mode": "updated"})
    epoch, events = store.get("stream_id"), store.changes()
    backup = tmp_path / "backup.sqlite3"
    store.backup(backup)
    result = store.expand_scope(30, now_unix=3000000)
    assert result["changed"] and result["scope_start_unix"] == 3000000 - 30 * 86400
    assert store.stats()["review_count"] == 1 and store.get("stream_id") == epoch
    assert store.changes() == events
    assert store.get("checkpoint_recent") is None and store.get("checkpoint_updated") is None
    assert store.get("next_mode") == "recent" and store.get("backfill_status") == "pending"
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1
        assert json.loads(db.execute("SELECT value FROM metadata WHERE key='scope_start_unix'").fetchone()[0]) == old_start


def test_existing_earlier_scope_and_repeat_request_are_no_ops(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    result = store.expand_scope(30, now_unix=3000000)
    assert result["changed"]
    store.set("checkpoint_recent", {"cursor": "in-progress"})
    assert store.expand_scope(30, now_unix=3000001)["changed"] is False
    assert store.get("checkpoint_recent") == {"cursor": "in-progress"}
    assert store.expand_scope(7, now_unix=3000001)["changed"] is False


def test_empty_database_scope_is_clamped_and_invalid_days_rejected(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    assert store.expand_scope(30, now_unix=1000)["scope_start_unix"] == 0
    for days in [0, -1, True, 3.5]:
        with pytest.raises(ValueError):
            store.expand_scope(days, now_unix=1000)


def test_backfill_completes_only_after_both_modes(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.expand_scope(30, now_unix=3000000)
    class Empty:
        def fetch(self, **kwargs): return {"reviews": []}
        def close(self): pass
    collector = Collector(store, Settings(max_pages_per_cycle=1), Empty(), clock=lambda: 3000000)
    assert not collector.collect_once()["sweep_complete"]
    assert store.get("backfill_status") == "running"
    assert collector.collect_once()["sweep_complete"]
    assert store.get("backfill_status") == "completed"
    assert store.get("backfill_completed_at") is not None


@pytest.mark.parametrize("elapsed,delay", [(0,600),(8,592),(599,1),(600,0),(700,0),(-1,600)])
def test_start_to_start_schedule(elapsed, delay):
    assert cycle_delay(600, elapsed) == delay


@pytest.mark.parametrize("incomplete,fails,elapsed,expected", [(False,False,8,592),(True,False,8,1),(True,True,8,592),(False,False,700,0)])
def test_background_scheduler_waits_without_overlapping_jobs(tmp_path, monkeypatch, incomplete, fails, elapsed, expected):
    import cs2_reviews.collector as module
    store = Store(tmp_path / "db.sqlite3")
    waits, calls = [], []
    class Event:
        stopped = False
        def is_set(self): return self.stopped
        def wait(self, duration):
            waits.append(duration)
            self.stopped = True
            return True
        def set(self): self.stopped = True
    class FakeCollector:
        def __init__(self, *args): pass
        def collect_once(self, stop):
            calls.append("one")
            if incomplete: store.set("checkpoint_recent", {"cursor": "pending"})
            if fails: raise RuntimeError("temporary network error")
        def close(self): pass
    values = iter([100,100+elapsed,100+elapsed])
    monkeypatch.setattr(module, "Collector", FakeCollector)
    monkeypatch.setattr(module.time, "monotonic", lambda: next(values))
    runner = module.BackgroundCollector(store, Settings())
    runner.stop_event = Event()
    runner._run()
    assert waits == [expected] and calls == ["one"]
    assert store.get("cycle_in_progress") is False and store.get("next_run_at") is None

