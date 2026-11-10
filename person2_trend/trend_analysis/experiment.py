"""Reproducible fixture for the trend score.

The reviews are constructed. They show how a surge, a stable word, a
new word, and a one-off word are treated. They are not Steam results.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .detector import detect_trends


FIXTURE_END = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def _insert(db, app_id, review_id, created_at, tokens, usable=True):
    review = {
        "app_id": app_id,
        "review_id": review_id,
        "revision": 1,
        "created_at": created_at,
        "tokens_trend": tokens,
        "usable_for_trend": usable,
    }
    db.execute(
        "INSERT INTO reviews(review_id, revision, record_json) VALUES (?, ?, ?)",
        (review_id, 1, json.dumps(review)),
    )


def _open(data_root, app_id, consumer_name):
    if app_id == 730:
        path = data_root / f"consumer_{consumer_name}.sqlite3"
    else:
        path = data_root / "games" / str(app_id) / f"consumer_{consumer_name}.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE reviews("
        "review_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, record_json TEXT NOT NULL)"
    )
    return db


def build_fixture(data_root, consumer_name="trend_fixture", end=FIXTURE_END):
    """Write a CS2 snapshot and a Dota 2 snapshot that must stay separate."""
    data_root = Path(data_root)
    recent_at = (end - timedelta(minutes=30)).isoformat()
    baseline_at = (end - timedelta(hours=12)).isoformat()

    with closing(_open(data_root, 730, consumer_name)) as db, db:
        for index in range(20):
            _insert(db, 730, f"base-server-{index}", baseline_at, ["server", "play"])
        for index in range(4):
            _insert(db, 730, f"base-lag-{index}", baseline_at, ["lag", "play"])
        for index in range(16):
            _insert(db, 730, f"base-play-{index}", baseline_at, ["play"])
        for index in range(8):
            _insert(db, 730, f"recent-both-{index}", recent_at, ["server", "lag"])
        for index in range(4):
            _insert(db, 730, f"recent-lag-{index}", recent_at, ["lag"])
        for index in range(6):
            _insert(db, 730, f"recent-vac-{index}", recent_at, ["vac"])
        _insert(db, 730, "recent-server-extra", recent_at, ["server"])
        _insert(db, 730, "recent-gg", recent_at, ["gg"])
        _insert(db, 730, "recent-spam", recent_at, ["spam"], usable=False)

    with closing(_open(data_root, 570, consumer_name)) as db, db:
        _insert(db, 570, "dota-recent", recent_at, ["roshan"])

    return {
        "end": end,
        "example_review": {
            "app_id": 730,
            "review_id": "recent-both-0",
            "created_at": recent_at,
            "usable_for_trend": True,
            "tokens_trend": ["server", "lag"],
            "note": "Constructed fixture review, not a Steam review.",
        },
    }


def run_fixture(data_root, consumer_name="trend_fixture"):
    """Score the fixture once with the default threshold and once with a stricter one."""
    built = build_fixture(data_root, consumer_name)
    common = {
        "app_id": 730,
        "consumer_name": consumer_name,
        "end": built["end"],
        "data_root": data_root,
        "computed_at": built["end"],
    }
    default = detect_trends(**common)
    strict = detect_trends(**common, parameters={"strength_threshold": 10})
    low_support = detect_trends(**common, parameters={"min_recent_df": 1})
    other_game = detect_trends(
        app_id=570,
        consumer_name=consumer_name,
        end=built["end"],
        data_root=data_root,
        computed_at=built["end"],
    )
    by_term = {item["term"]: item for item in default["trends"]}
    return {
        "label": "constructed_fixture_not_steam",
        "example_review": built["example_review"],
        "example_output": {
            "server": by_term.get("server"),
            "lag": by_term.get("lag"),
        },
        "default": default,
        "strength_threshold_10": {
            "trending_terms": strict["trending_terms"],
            "data_status": strict["data_status"],
        },
        "min_recent_df_1": {
            "trending_terms": low_support["trending_terms"],
        },
        "other_game": {
            "app_id": other_game["app_id"],
            "data_status": other_game["data_status"],
            "terms": [item["term"] for item in other_game["trends"]],
        },
    }
