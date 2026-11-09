from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
from typing import Callable

from .entities import EntityConfig, load_entity_config, match_entities
from .sentiment import SentimentAnalyzer
from .settings import (
    ALGORITHM_NAME,
    ROOT,
    SUPPORTED_PIPELINES,
    analysis_db_path,
    validate_app_id,
    validate_consumer_name,
)
from .util import iso_utc, parse_utc, utc_now


def _load_example_consumer():
    path = ROOT / "examples" / "consume_api.py"
    spec = importlib.util.spec_from_file_location("_steam_consume_api", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load consumer helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONSUMER = _load_example_consumer()


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def init_schema(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS reviews(review_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,record_json TEXT NOT NULL)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS review_status(
            review_id TEXT PRIMARY KEY,
            revision INTEGER NOT NULL,
            usable_for_sentiment INTEGER NOT NULL,
            pipeline_version TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            analyzed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reused_from_revision INTEGER
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS analysis(
            review_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            entity_id TEXT NOT NULL,
            sentiment_label TEXT NOT NULL,
            sentiment_score REAL NOT NULL,
            evidence TEXT NOT NULL,
            created_at TEXT NOT NULL,
            pipeline_version TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            entity_config_version TEXT NOT NULL,
            analyzed_at TEXT NOT NULL,
            reused_from_revision INTEGER,
            PRIMARY KEY(review_id, revision, entity_id, algorithm_version)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS analysis_entity_time ON analysis(entity_id, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS analysis_review ON analysis(review_id)")
    db.commit()


def state_get(db: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def state_set(db: sqlite3.Connection, key: str, value: str | None) -> None:
    if value is None:
        db.execute("DELETE FROM state WHERE key=?", (key,))
    else:
        db.execute("INSERT INTO state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def state_json(db: sqlite3.Connection, key: str, default=None):
    value = state_get(db, key)
    if value is None:
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default


def bind_state(db: sqlite3.Connection, app_id: int, stream_id: str, mode: str) -> None:
    validate_app_id(app_id)
    if not isinstance(stream_id, str) or not stream_id:
        raise ValueError("stream_id must be a non-empty string")
    if mode not in {"live", "sample"}:
        raise ValueError("mode must be live or sample")
    bound_app = state_get(db, "app_id")
    if bound_app and bound_app != str(app_id):
        raise ValueError("Analysis database app_id mismatch")
    bound_stream = state_get(db, "stream_id")
    if bound_stream and bound_stream != stream_id:
        raise ValueError("Stream changed: use a new consumer or analysis database")
    bound_mode = state_get(db, "mode")
    if bound_mode and bound_mode != mode:
        raise ValueError("Mode changed: use a separate consumer for sample/live")
    state_set(db, "app_id", str(app_id))
    state_set(db, "stream_id", stream_id)
    state_set(db, "mode", mode)
    if state_get(db, "cursor") is None:
        state_set(db, "cursor", "0")
    db.commit()


def ensure_algorithm_binding(
    db: sqlite3.Connection,
    config: EntityConfig,
    algorithm_version: str,
    lexicon_hash: str | None = None,
) -> None:
    stored_version = state_get(db, "algorithm_version")
    stored_hash = state_get(db, "entity_config_hash")
    stored_lexicon = state_get(db, "lexicon_hash")
    if stored_version and stored_version != algorithm_version:
        raise ValueError("Algorithm version changed; run rebuild before continuing")
    if stored_hash and stored_hash != config.content_hash:
        raise ValueError("Entity config changed; run rebuild before continuing")
    if lexicon_hash and stored_lexicon and stored_lexicon != lexicon_hash:
        raise ValueError("Domain lexicon changed; run rebuild before continuing")


def record_algorithm_binding(
    db: sqlite3.Connection,
    config: EntityConfig,
    algorithm_version: str,
    lexicon_hash: str | None = None,
) -> None:
    state_set(db, "algorithm_version", algorithm_version)
    state_set(db, "entity_config_hash", config.content_hash)
    state_set(db, "entity_config_version", config.version)
    state_set(db, "algorithm_name", ALGORITHM_NAME)
    if lexicon_hash:
        state_set(db, "lexicon_hash", lexicon_hash)


def record_health(db: sqlite3.Connection, health: dict, mode: str) -> None:
    state_set(db, "health_json", json.dumps(health, ensure_ascii=False, sort_keys=True))
    state_set(db, "health_fetched_at", iso_utc(utc_now()))
    state_set(db, "mode", mode)
    db.commit()


def get_health(db: sqlite3.Connection) -> tuple[dict, str | None]:
    health = state_json(db, "health_json", {})
    return (health if isinstance(health, dict) else {}), state_get(db, "health_fetched_at")


def _fingerprint(record: dict, config: EntityConfig, algorithm_version: str, analyzer: SentimentAnalyzer) -> str:
    payload = {
        "text_clean": record.get("text_clean", ""),
        "created_at": record.get("created_at"),
        "usable_for_sentiment": record.get("usable_for_sentiment"),
        "pipeline_version": record.get("pipeline_version"),
        "algorithm_version": algorithm_version,
        "analyzer_version": analyzer.version,
        "lexicon_hash": analyzer.lexicon_hash,
        "entity_config_hash": config.content_hash,
        "positive_threshold": 0.05,
        "negative_threshold": -0.05,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _delete_analysis(db: sqlite3.Connection, review_id: str) -> None:
    db.execute("DELETE FROM analysis WHERE review_id=?", (review_id,))


def _reuse_analysis(
    db: sqlite3.Connection,
    review_id: str,
    old_revision: int,
    new_revision: int,
    algorithm_version: str,
) -> None:
    rows = db.execute(
        """
        SELECT entity_id,sentiment_label,sentiment_score,evidence,created_at,pipeline_version,
               algorithm_version,entity_config_version,analyzed_at
        FROM analysis WHERE review_id=? AND revision=? AND algorithm_version=?
        """,
        (review_id, old_revision, algorithm_version),
    ).fetchall()
    _delete_analysis(db, review_id)
    for row in rows:
        db.execute(
            "INSERT INTO analysis VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                review_id,
                new_revision,
                row["entity_id"],
                row["sentiment_label"],
                row["sentiment_score"],
                row["evidence"],
                row["created_at"],
                row["pipeline_version"],
                row["algorithm_version"],
                row["entity_config_version"],
                row["analyzed_at"],
                old_revision,
            ),
        )


def _write_status(
    db: sqlite3.Connection,
    record: dict,
    fingerprint: str,
    status: str,
    now_text: str,
    *,
    reused_from_revision: int | None = None,
) -> None:
    db.execute(
        """
        INSERT INTO review_status(
            review_id,revision,usable_for_sentiment,pipeline_version,input_fingerprint,
            status,analyzed_at,updated_at,reused_from_revision
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(review_id) DO UPDATE SET
            revision=excluded.revision,
            usable_for_sentiment=excluded.usable_for_sentiment,
            pipeline_version=excluded.pipeline_version,
            input_fingerprint=excluded.input_fingerprint,
            status=excluded.status,
            analyzed_at=excluded.analyzed_at,
            updated_at=excluded.updated_at,
            reused_from_revision=excluded.reused_from_revision
        """,
        (
            record["review_id"],
            record["revision"],
            int(bool(record.get("usable_for_sentiment"))),
            record["pipeline_version"],
            fingerprint,
            status,
            now_text,
            now_text,
            reused_from_revision,
        ),
    )


def apply_upsert(
    db: sqlite3.Connection,
    previous: dict | None,
    current: dict,
    *,
    config: EntityConfig,
    analyzer: SentimentAnalyzer,
    algorithm_version: str,
    force: bool = False,
) -> None:
    validate_app_id(current["app_id"])
    pipeline = current.get("pipeline_version")
    if pipeline not in SUPPORTED_PIPELINES:
        raise ValueError(f"Unsupported pipeline version: {pipeline}")
    created_at = current.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        raise ValueError("Review requires created_at")
    created_text = iso_utc(parse_utc(created_at))
    if not isinstance(current.get("text_clean"), str):
        raise ValueError("Review requires text_clean string")
    if not isinstance(current.get("usable_for_sentiment"), bool):
        raise ValueError("Review requires usable_for_sentiment boolean")
    review_id = current["review_id"]
    revision = int(current["revision"])
    usable = current["usable_for_sentiment"]
    text = current["text_clean"]
    fingerprint = _fingerprint(current, config, algorithm_version, analyzer)
    now_text = iso_utc(utc_now())

    old = db.execute(
        "SELECT revision,input_fingerprint,status,analyzed_at FROM review_status WHERE review_id=?",
        (review_id,),
    ).fetchone()

    if not force and old and old["input_fingerprint"] == fingerprint and old["status"] in {
        "analyzed", "excluded_unusable", "skipped_empty"
    }:
        if old["status"] == "analyzed":
            _reuse_analysis(db, review_id, int(old["revision"]), revision, algorithm_version)
        else:
            _delete_analysis(db, review_id)
        db.execute(
            "UPDATE review_status SET revision=?,updated_at=?,reused_from_revision=? WHERE review_id=?",
            (revision, now_text, int(old["revision"]), review_id),
        )
        return

    _delete_analysis(db, review_id)

    if not usable:
        _write_status(db, current, fingerprint, "excluded_unusable", now_text)
        return
    if not text.strip():
        _write_status(db, current, fingerprint, "skipped_empty", now_text)
        return

    matches = match_entities(text, config)
    analyzed_at = now_text
    for match in matches:
        score, label = analyzer.score_match(match)
        db.execute(
            "INSERT INTO analysis VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                review_id,
                revision,
                match.entity.entity_id,
                label,
                score,
                match.evidence,
                created_text,
                pipeline,
                algorithm_version,
                config.version,
                analyzed_at,
                None,
            ),
        )
    _write_status(db, current, fingerprint, "analyzed", now_text)


def save_batch(
    db: sqlite3.Connection,
    batch: dict,
    cursor: int,
    *,
    app_id: int,
    config: EntityConfig,
    analyzer: SentimentAnalyzer,
    algorithm_version: str,
) -> tuple[int, int]:
    def on_upsert(connection: sqlite3.Connection, previous: dict | None, current: dict) -> None:
        apply_upsert(
            connection,
            previous,
            current,
            config=config,
            analyzer=analyzer,
            algorithm_version=algorithm_version,
        )

    return CONSUMER.save_batch(db, batch, cursor, app_id=app_id, on_upsert=on_upsert)


def rebuild(
    db: sqlite3.Connection,
    *,
    app_id: int,
    config: EntityConfig,
    analyzer: SentimentAnalyzer,
    algorithm_version: str,
) -> int:
    records = []
    for row in db.execute("SELECT record_json FROM reviews ORDER BY review_id"):
        record = json.loads(row["record_json"])
        if record.get("app_id") != app_id:
            raise ValueError("Stored review app_id mismatch during rebuild")
        records.append(record)
    with db:
        for record in records:
            apply_upsert(
                db,
                None,
                record,
                config=config,
                analyzer=analyzer,
                algorithm_version=algorithm_version,
                force=True,
            )
        record_algorithm_binding(db, config, algorithm_version, analyzer.lexicon_hash)
    db.commit()
    return len(records)


def analysis_rows(db: sqlite3.Connection, algorithm_version: str) -> list[sqlite3.Row]:
    return list(db.execute(
        """
        SELECT a.*, s.status AS review_status, s.usable_for_sentiment
        FROM analysis a
        JOIN review_status s ON s.review_id=a.review_id AND s.revision=a.revision
        WHERE a.algorithm_version=? AND s.status='analyzed'
        ORDER BY a.created_at, a.review_id, a.entity_id
        """,
        (algorithm_version,),
    ))


def current_records(db: sqlite3.Connection) -> list[dict]:
    return [json.loads(row["record_json"]) for row in db.execute("SELECT record_json FROM reviews ORDER BY review_id")]
