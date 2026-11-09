from __future__ import annotations

import json

from sentiment_analysis.entities import load_entity_config
from sentiment_analysis.sentiment import SentimentAnalyzer
from sentiment_analysis.settings import ALGORITHM_VERSION
from sentiment_analysis.store import (
    bind_state,
    connect,
    ensure_algorithm_binding,
    init_schema,
    record_algorithm_binding,
)

STREAM = "stream-1"


def record(
    review_id: str = "123",
    revision: int = 1,
    text: str = "CS2 is fun but VAC is bad.",
    usable: bool = True,
    created_at: str = "2026-11-09T00:00:00Z",
    votes_up: int | None = 1,
    pipeline_version: str = "clean-en-v3",
    app_id: int = 730,
) -> dict:
    return {
        "schema_version": "1.0",
        "source": "steam",
        "app_id": app_id,
        "review_id": review_id,
        "created_at": created_at,
        "updated_at": created_at,
        "first_seen_at": created_at,
        "last_seen_at": created_at,
        "language": "english",
        "language_check": "passed",
        "text_raw": text,
        "text_clean": text,
        "tokens_trend": [],
        "steam_recommended": True,
        "votes_up": votes_up,
        "comment_count": 0,
        "quality_flags": [],
        "usable_for_trend": True,
        "usable_for_sentiment": usable,
        "pipeline_version": pipeline_version,
        "revision": revision,
    }


def batch(item: dict, change_id: int = 1, stream: str = STREAM) -> dict:
    return {
        "schema_version": "1.0",
        "stream_id": stream,
        "items": [{
            "change_id": change_id,
            "operation": "upsert",
            "review_id": item["review_id"],
            "revision": item["revision"],
            "review": item,
        }],
        "next_cursor": str(change_id),
        "has_more": False,
    }


def setup(tmp_path):
    db = connect(tmp_path / "analysis.sqlite3")
    init_schema(db)
    bind_state(db, 730, STREAM, "live")
    config = load_entity_config(730)
    ensure_algorithm_binding(db, config, ALGORITHM_VERSION)
    record_algorithm_binding(db, config, ALGORITHM_VERSION)
    db.commit()
    return db, config, SentimentAnalyzer()


def setup_empty_state(tmp_path):
    db = connect(tmp_path / "analysis.sqlite3")
    init_schema(db)
    db.execute("INSERT OR REPLACE INTO state VALUES('mode','live')")
    db.execute("INSERT OR REPLACE INTO state VALUES('stream_id','stream-1')")
    db.execute("INSERT OR REPLACE INTO state VALUES('cursor','10')")
    db.commit()
    return db, load_entity_config(730)


def insert_analysis(
    db,
    review_id: str,
    entity_id: str,
    label: str,
    created_at: str,
    *,
    revision: int = 1,
    score: float = 0.0,
    pipeline_version: str = "clean-en-v3",
):
    db.execute(
        """
        INSERT INTO review_status(
            review_id,revision,usable_for_sentiment,pipeline_version,input_fingerprint,
            status,analyzed_at,updated_at,reused_from_revision
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(review_id) DO NOTHING
        """,
        (review_id, revision, 1, pipeline_version, f"fp-{review_id}", "analyzed",
         "2026-11-09T00:00:00Z", "2026-11-09T00:00:00Z", None),
    )
    db.execute(
        "INSERT OR REPLACE INTO analysis VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (review_id, revision, entity_id, label, score, "evidence", created_at,
         pipeline_version, ALGORITHM_VERSION, "entities-v1", "2026-11-09T00:00:00Z", None),
    )


def dump_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
