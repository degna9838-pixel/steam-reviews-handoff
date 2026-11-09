from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import get_game

SCHEMA_VERSION = "1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def unix_to_iso(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError("Source timestamp must be non-negative Unix seconds")
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _source_record(raw: dict, app_id: int = 730) -> dict:
    """Build the stable payload shared by ingestion and reprocessing."""
    from .cleaning import clean_review_text
    if not isinstance(raw, dict):
        raise ValueError("Review must be an object")
    review_id = str(raw.get("recommendationid", ""))
    if not review_id or not review_id.isdecimal():
        raise ValueError("Review has no valid recommendationid")
    if not isinstance(raw.get("review"), str) or not isinstance(raw.get("voted_up"), bool):
        raise ValueError(f"Review {review_id}: missing text or recommendation flag")
    engagement = {}
    for field in ("votes_up", "comment_count"):
        value = raw.get(field)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"Review {review_id}: {field} must be a non-negative integer or null")
        engagement[field] = value
    return {
        "schema_version": SCHEMA_VERSION, "source": "steam", "app_id": app_id,
        "review_id": review_id,
        "created_at": unix_to_iso(raw.get("timestamp_created")),
        "updated_at": unix_to_iso(raw.get("timestamp_updated")),
        "language": str(raw.get("language", "unknown")),
        "steam_recommended": raw["voted_up"],
        **engagement,
        **clean_review_text(raw["review"], str(raw.get("language", "unknown"))),
    }


def _stable_record(record: dict) -> dict:
    return {key: value for key, value in record.items()
            if key not in {"first_seen_at", "last_seen_at", "revision"}}


def _signature(record: dict) -> str:
    return hashlib.sha256(json.dumps(_stable_record(record), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: Path, *, app_id: int = 730):
        self.app_id = get_game(app_id).app_id
        self.path = Path(path)
        if self.path.is_file() and self.path.stat().st_size:
            uri = self.path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=30)) as probe:
                self._validate_game_binding(probe, self.app_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_id TEXT UNIQUE NOT NULL,
                    record_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    revision INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS changes (
                    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    record_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    UNIQUE(review_id, revision)
                );
                CREATE INDEX IF NOT EXISTS changes_review ON changes(review_id);
            """)
            db.execute("BEGIN IMMEDIATE")
            self._validate_game_binding(db, self.app_id)
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('stream_id', ?)", (json.dumps(str(uuid.uuid4())),))
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('app_id', ?)", (json.dumps(self.app_id),))

    @staticmethod
    def _validate_game_binding(db: sqlite3.Connection, app_id: int) -> None:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "metadata" in tables:
            row = db.execute("SELECT value FROM metadata WHERE key='app_id'").fetchone()
            if row is not None:
                bound = json.loads(row[0])
                if type(bound) is not int or bound != app_id:
                    raise ValueError(f"Database is bound to app_id {bound!r}, not {app_id}")
                return
        for table in ("reviews", "changes"):
            if table not in tables:
                continue
            for row in db.execute(f"SELECT record_json FROM {table}"):
                record = json.loads(row[0])
                actual = record.get("app_id") if isinstance(record, dict) else None
                if type(actual) is not int or actual != app_id:
                    raise ValueError(f"Legacy {table} contains app_id {actual!r}, not {app_id}; refusing to modify database")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.path), timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _set(db: sqlite3.Connection, key: str, value: Any) -> None:
        db.execute("INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def set(self, key: str, value: Any) -> None:
        with self.connect() as db:
            self._set(db, key, value)

    def set_many(self, values: dict) -> None:
        with self.connect() as db:
            for key, value in values.items():
                self._set(db, key, value)

    def backup(self, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(target) as destination:
            source.backup(destination)

    def expand_scope(self, days: int, *, now_unix: int | None = None) -> dict:
        """Call under CollectorLock. Preserve records, revisions, stream and change IDs."""
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise ValueError("Backfill days must be a positive integer")
        now_unix = int(time.time()) if now_unix is None else now_unix
        requested = max(0, now_unix - days * 86400)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM metadata WHERE key='scope_start_unix'").fetchone()
            old = json.loads(row[0]) if row else None
            if old is not None and old <= requested:
                return {"changed": False, "scope_start_unix": old, "requested_start_unix": requested,
                        "reason": "Existing scope already covers the requested history"}
            start = requested if old is None else min(old, requested)
            values = {
                "scope_start_unix": start, "checkpoint_recent": None, "checkpoint_updated": None,
                "watermark_recent": start, "watermark_updated": start, "next_mode": "recent",
                "reconcile_pending": False, "last_reconcile_unix": None,
                "backfill_status": "pending", "backfill_target_days": days,
                "backfill_requested_at": unix_to_iso(now_unix), "backfill_started_at": None,
                "backfill_completed_at": None, "last_complete_sweep_at": None,
                "next_run_at": None, "active_scan": None,
            }
            for key, value in values.items():
                self._set(db, key, value)
        return {"changed": True, "previous_scope_start_unix": old, "scope_start_unix": start,
                "scope_start_at": unix_to_iso(start), "backfill_days": days}

    def get(self, key: str, default: Any = None) -> Any:
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def apply_page(self, raw_reviews: list[dict], *, now: str | None = None, state: dict | None = None) -> dict:
        now = now or utc_now()
        prepared = []
        for raw in raw_reviews:
            record = _source_record(raw, self.app_id)
            prepared.append((record["review_id"], record, _signature(record), raw))
        counts = {"received": len(prepared), "inserted": 0, "updated": 0, "unchanged": 0}
        with self.connect() as db:
            for review_id, record, signature, raw in prepared:
                old = db.execute("SELECT * FROM reviews WHERE review_id=?", (review_id,)).fetchone()
                raw_json = json.dumps(raw, ensure_ascii=False)
                if old:
                    old_record = json.loads(old["record_json"])
                    # Cached/replayed pages must never roll a newer source version back.
                    if record["updated_at"] < old_record["updated_at"]:
                        old_record["last_seen_at"] = now
                        db.execute("UPDATE reviews SET record_json=? WHERE review_id=?", (json.dumps(old_record, ensure_ascii=False), review_id))
                        counts["unchanged"] += 1
                        continue
                    if old["signature"] == signature or _stable_record(old_record) == record:
                        old_record["last_seen_at"] = now
                        db.execute("UPDATE reviews SET record_json=?,raw_json=?,signature=? WHERE review_id=?", (json.dumps(old_record, ensure_ascii=False), raw_json, signature, review_id))
                        counts["unchanged"] += 1
                        continue
                    revision = old["revision"] + 1
                    first_seen = old_record["first_seen_at"]
                    counts["updated"] += 1
                else:
                    revision, first_seen = 1, now
                    counts["inserted"] += 1
                record.update(first_seen_at=first_seen, last_seen_at=now, revision=revision)
                record_json = json.dumps(record, ensure_ascii=False)
                db.execute("""INSERT INTO reviews(review_id,record_json,raw_json,signature,revision)
                    VALUES(?,?,?,?,?) ON CONFLICT(review_id) DO UPDATE SET
                    record_json=excluded.record_json,raw_json=excluded.raw_json,
                    signature=excluded.signature,revision=excluded.revision""", (review_id, record_json, raw_json, signature, revision))
                db.execute("INSERT INTO changes(review_id,revision,record_json,observed_at) VALUES(?,?,?,?)", (review_id, revision, record_json, now))
            for key, value in (state or {}).items():
                self._set(db, key, value)
        return counts

    def pipeline_versions(self) -> list[str]:
        with self.connect() as db:
            rows = db.execute("SELECT DISTINCT json_extract(record_json, '$.pipeline_version') AS version FROM reviews ORDER BY version").fetchall()
        return [row["version"] for row in rows if isinstance(row["version"], str)]

    def needs_reprocess(self) -> bool:
        from .cleaning import PIPELINE_VERSION
        with self.connect() as db:
            return db.execute("""SELECT 1 FROM reviews
                WHERE json_extract(record_json, '$.pipeline_version') IS NULL
                   OR json_extract(record_json, '$.pipeline_version') != ?
                   OR json_type(record_json, '$.votes_up') IS NULL
                   OR json_type(record_json, '$.comment_count') IS NULL
                LIMIT 1""", (PIPELINE_VERSION,)).fetchone() is not None

    def reprocess(self, *, now: str | None = None) -> dict:
        """Call under CollectorLock. Keep source/observation times and scan progress."""
        from .cleaning import PIPELINE_VERSION
        now = now or utc_now()
        counts = {"processed": 0, "updated": 0, "unchanged": 0,
                  "pipeline_version": PIPELINE_VERSION, "reprocessed_at": now}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM reviews ORDER BY id").fetchall()
            for row in rows:
                old = json.loads(row["record_json"])
                record = _source_record(json.loads(row["raw_json"]), self.app_id)
                if record["review_id"] != row["review_id"]:
                    raise ValueError(f"Stored raw identity mismatch for {row['review_id']}")
                record["created_at"] = old["created_at"]
                record["updated_at"] = old["updated_at"]
                signature = _signature(record)
                counts["processed"] += 1
                if record == _stable_record(old):
                    db.execute("UPDATE reviews SET signature=? WHERE review_id=?", (signature, row["review_id"]))
                    counts["unchanged"] += 1
                    continue
                record.update(first_seen_at=old["first_seen_at"], last_seen_at=old["last_seen_at"],
                              revision=row["revision"] + 1)
                record_json = json.dumps(record, ensure_ascii=False)
                db.execute("UPDATE reviews SET record_json=?,signature=?,revision=? WHERE review_id=?",
                           (record_json, signature, record["revision"], row["review_id"]))
                db.execute("INSERT INTO changes(review_id,revision,record_json,observed_at) VALUES(?,?,?,?)",
                           (row["review_id"], record["revision"], record_json, now))
                counts["updated"] += 1
            self._set(db, "last_reprocess_at", now)
            self._set(db, "last_reprocess_pipeline_version", PIPELINE_VERSION)
            self._set(db, "last_reprocess_counts", counts)
        return counts

    def current(self, after_id: int = 0, limit: int = 100, usable_for: str | None = None) -> dict:
        # A transaction binds page rows and the accompanying head to one DB snapshot.
        with self.connect() as db:
            db.execute("BEGIN")
            clauses, params = ["id > ?"], [after_id]
            if usable_for:
                clauses.append("json_extract(record_json, ?) = 1")
                params.append("$.usable_for_" + usable_for)
            rows = db.execute("SELECT id,record_json FROM reviews WHERE " + " AND ".join(clauses) + " ORDER BY id LIMIT ?", [*params, limit + 1]).fetchall()
            head = db.execute("SELECT COALESCE(MAX(change_id),0) FROM changes").fetchone()[0]
            stream = json.loads(db.execute("SELECT value FROM metadata WHERE key='stream_id'").fetchone()[0])
        selected = rows[:limit]
        return {"schema_version": SCHEMA_VERSION, "stream_id": stream,
                "items": [json.loads(row["record_json"]) for row in selected],
                "next_after_id": selected[-1]["id"] if selected else after_id,
                "has_more": len(rows) > limit, "snapshot_cursor": str(head)}

    def changes(self, cursor: int = 0, limit: int = 100) -> dict:
        with self.connect() as db:
            db.execute("BEGIN")
            head = db.execute("SELECT COALESCE(MAX(change_id),0) FROM changes").fetchone()[0]
            if cursor > head:
                raise ValueError("Cursor is beyond this stream; verify stream_id or reset cursor to 0")
            rows = db.execute("SELECT * FROM changes WHERE change_id>? ORDER BY change_id LIMIT ?", (cursor, limit + 1)).fetchall()
            stream = json.loads(db.execute("SELECT value FROM metadata WHERE key='stream_id'").fetchone()[0])
        selected = rows[:limit]
        return {"schema_version": SCHEMA_VERSION, "stream_id": stream,
                "items": [{"change_id": row["change_id"], "operation": "upsert", "review_id": row["review_id"],
                           "revision": row["revision"], "review": json.loads(row["record_json"])} for row in selected],
                "next_cursor": str(selected[-1]["change_id"] if selected else cursor), "has_more": len(rows) > limit}

    def review(self, review_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT record_json FROM reviews WHERE review_id=?", (review_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def stats(self) -> dict:
        with self.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
            changes = db.execute("SELECT COALESCE(MAX(change_id),0) FROM changes").fetchone()[0]
            meta = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM metadata")}
            versions = [r[0] for r in db.execute("SELECT DISTINCT json_extract(record_json, '$.pipeline_version') FROM reviews ORDER BY 1")]
        return {**meta, "review_count": count, "latest_cursor": str(changes), "pipeline_versions": versions}

    def export(self, target: Path) -> int:
        """Publish one read snapshot. Verify the JSONL hash before trusting its cursor."""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        manifest_path = target.with_suffix(".manifest.json")
        manifest_temporary = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with self.connect() as db, temporary.open("wb") as out:
                db.execute("BEGIN")
                metadata = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM metadata")}
                head = db.execute("SELECT COALESCE(MAX(change_id),0) FROM changes").fetchone()[0]
                exported_at = utc_now()
                count = 0
                versions, digest = set(), hashlib.sha256()
                for row in db.execute("SELECT record_json FROM reviews ORDER BY id"):
                    payload = (row[0] + "\n").encode("utf-8")
                    out.write(payload)
                    digest.update(payload)
                    version = json.loads(row[0]).get("pipeline_version")
                    if isinstance(version, str):
                        versions.add(version)
                    count += 1
                out.flush()
                os.fsync(out.fileno())
                scope = metadata.get("scope_start_unix")
                sample = (metadata.get("dataset_type") == "synthetic_examples_not_real_steam_reviews"
                          or self.path.name == "sample.sqlite3")
                manifest = {"schema_version": SCHEMA_VERSION, "stream_id": metadata["stream_id"], "app_id": self.app_id,
                            "mode": "sample" if sample else "live", "pipeline_versions": sorted(versions),
                            "exported_at": exported_at, "scope_start_at": unix_to_iso(scope) if scope is not None else None,
                            "backfill_status": metadata.get("backfill_status"), "snapshot_cursor": str(head),
                            "row_count": count, "jsonl_file": target.name, "jsonl_sha256": digest.hexdigest()}
                with manifest_temporary.open("wb") as manifest_out:
                    manifest_out.write((json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
                    manifest_out.flush()
                    os.fsync(manifest_out.fileno())
            temporary.replace(target)
            manifest_temporary.replace(manifest_path)
            return count
        finally:
            temporary.unlink(missing_ok=True)
            manifest_temporary.unlink(missing_ok=True)
