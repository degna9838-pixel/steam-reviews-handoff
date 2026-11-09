import json
import sqlite3

import pytest

from cs2_reviews.maintenance import ensure_current_cleaning
from cs2_reviews.storage import Store


def test_upgrade_backs_up_before_changing_and_then_is_noop(tmp_path):
    store = Store(tmp_path / "reviews.sqlite3")
    raw = {"recommendationid": "1", "review": "Good game", "language": "english",
           "timestamp_created": 1, "timestamp_updated": 2, "voted_up": True}
    store.apply_page([raw])
    with store.connect() as db:
        old = json.loads(db.execute("SELECT record_json FROM reviews").fetchone()[0])
        old["pipeline_version"] = "clean-en-v1"
        db.execute("UPDATE reviews SET record_json=?", (json.dumps(old),))
    result = ensure_current_cleaning(store)
    with sqlite3.connect(result["backup"]) as db:
        assert json.loads(db.execute("SELECT record_json FROM reviews").fetchone()[0])["pipeline_version"] == "clean-en-v1"
    assert store.review("1")["pipeline_version"] == "clean-en-v3"
    assert ensure_current_cleaning(store) is None


def test_unknown_version_cannot_be_silently_downgraded(tmp_path):
    store = Store(tmp_path / "reviews.sqlite3")
    store.apply_page([{"recommendationid": "1", "review": "fun", "language": "english",
                       "timestamp_created": 1, "timestamp_updated": 2, "voted_up": True}])
    with store.connect() as db:
        old = json.loads(db.execute("SELECT record_json FROM reviews").fetchone()[0])
        old["pipeline_version"] = "future-v99"
        db.execute("UPDATE reviews SET record_json=?", (json.dumps(old),))
    with pytest.raises(ValueError, match="refusing"):
        ensure_current_cleaning(store)
    assert store.review("1")["pipeline_version"] == "future-v99"
