"""Standard-library example: persist latest reviews and an independent cursor."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_PIPELINES = {"clean-en-v1", "clean-en-v2", "clean-en-v3"}


def validate_app_id(app_id: int) -> None:
    if type(app_id) is not int or app_id <= 0:
        raise ValueError("app_id must be a positive integer")


def consumer_path(name: str, app_id: int = 730) -> Path:
    validate_app_id(app_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise ValueError("Invalid consumer name")
    directory = ROOT / "data" if app_id == 730 else ROOT / "data" / "games" / str(app_id)
    return directory / f"consumer_{name}.sqlite3"


def validate_game_binding(db: sqlite3.Connection, app_id: int) -> None:
    validate_app_id(app_id)
    bound = db.execute("SELECT value FROM state WHERE key='app_id'").fetchone()
    if bound and bound[0] != str(app_id):
        raise ValueError("Consumer app_id mismatch: use a separate database per game")
    if not bound:
        for row in db.execute("SELECT record_json FROM reviews"):
            validate_record(json.loads(row[0]), app_id=app_id)


def validate_record(record: dict, *, app_id: int = 730) -> None:
    validate_app_id(app_id)
    if not isinstance(record, dict):
        raise ValueError("Invalid review record")
    if type(record.get("app_id")) is not int or record["app_id"] != app_id:
        raise ValueError("Record app_id mismatch")
    if record.get("schema_version") != "1.0" or record.get("pipeline_version") not in SUPPORTED_PIPELINES:
        raise ValueError("Unsupported record schema/pipeline version; update the consumer before advancing")
    if not isinstance(record.get("review_id"), str) or not record["review_id"].isdecimal():
        raise ValueError("Invalid review_id")
    if type(record.get("revision")) is not int or record["revision"] < 1:
        raise ValueError("Invalid revision")
    for field in ("votes_up", "comment_count"):
        value = record.get(field)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"Invalid {field}: expected a non-negative integer or null")


def load_snapshot(db: sqlite3.Connection, path: Path, *, app_id: int = 730, on_upsert=None) -> tuple[int, int]:
    """Initialize an empty consumer from a verified paired snapshot, atomically.

    on_upsert(db, previous_record_or_none, record) may update your analysis tables.
    Both the records and the resume cursor commit together, or neither does.
    """
    validate_app_id(app_id)
    path = Path(path)
    manifest = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    payload = path.read_bytes()
    if manifest.get("jsonl_file") != path.name or manifest.get("jsonl_sha256") != hashlib.sha256(payload).hexdigest():
        raise ValueError("Snapshot/manifest mismatch: re-read both files before retrying")
    if manifest.get("schema_version") != "1.0" or not isinstance(manifest.get("stream_id"), str):
        raise ValueError("Unsupported snapshot schema/stream")
    if "app_id" in manifest and (type(manifest["app_id"]) is not int or manifest["app_id"] != app_id):
        raise ValueError("Snapshot manifest app_id mismatch")
    if manifest.get("mode") not in {"live", "sample"}:
        raise ValueError("Unknown snapshot mode")
    versions = manifest.get("pipeline_versions")
    if not isinstance(versions, list) or not set(versions) <= SUPPORTED_PIPELINES:
        raise ValueError("Unsupported snapshot cleaning version")
    cursor_text = manifest.get("snapshot_cursor")
    if not isinstance(cursor_text, str) or not cursor_text.isdecimal():
        raise ValueError("Invalid snapshot cursor")
    records = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]
    if type(manifest.get("row_count")) is not int or len(records) != manifest["row_count"]:
        raise ValueError("Snapshot row count mismatch")
    if {r.get("pipeline_version") for r in records} != set(versions):
        raise ValueError("Snapshot cleaning versions do not match records")
    if "app_id" not in manifest and not records and app_id != 730:
        raise ValueError("Legacy empty snapshot has no app_id; cannot bind it to this game")
    if db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] or db.execute("SELECT COUNT(*) FROM state").fetchone()[0]:
        raise ValueError("Snapshot initialization requires an empty consumer; choose a new name or explicitly --reset")
    with db:
        for record in records:
            validate_record(record, app_id=app_id)
            db.execute("INSERT INTO reviews VALUES(?,?,?)", (record["review_id"], record["revision"], json.dumps(record, ensure_ascii=False)))
            if on_upsert:
                on_upsert(db, None, record)
        for key, value in (("app_id", str(app_id)), ("stream_id", manifest["stream_id"]), ("cursor", cursor_text)):
            db.execute("INSERT INTO state VALUES(?,?)", (key, value))
    return int(cursor_text), len(records)


def save_batch(db: sqlite3.Connection, batch: dict, cursor: int, *, app_id: int = 730, on_upsert=None) -> tuple[int, int]:
    validate_game_binding(db, app_id)
    if "app_id" in batch and (type(batch["app_id"]) is not int or batch["app_id"] != app_id):
        raise ValueError("Batch app_id mismatch")
    if batch.get("schema_version") != "1.0" or not isinstance(batch.get("stream_id"), str):
        raise ValueError("Unsupported schema or missing stream_id")
    old_stream = db.execute("SELECT value FROM state WHERE key='stream_id'").fetchone()
    if old_stream and old_stream[0] != batch["stream_id"]:
        raise ValueError("Stream changed: choose a new consumer or explicitly use --reset")
    next_cursor = int(batch["next_cursor"])
    persisted = db.execute("SELECT value FROM state WHERE key='cursor'").fetchone()
    if persisted and next_cursor < int(persisted[0]):
        raise ValueError("Stale response would move the persisted cursor backwards")
    if next_cursor < cursor or not isinstance(batch.get("has_more"), bool):
        raise ValueError("Invalid API cursor/has_more")
    changed, last = 0, cursor
    with db:
        for event in batch["items"]:
            if event["operation"] != "upsert" or not last < event["change_id"] <= next_cursor:
                raise ValueError("Invalid change event order")
            last = event["change_id"]
            record = event["review"]
            validate_record(record, app_id=app_id)
            if record["review_id"] != event["review_id"] or record["revision"] != event["revision"]:
                raise ValueError("Event identity/version mismatch")
            old = db.execute("SELECT revision,record_json FROM reviews WHERE review_id=?", (event["review_id"],)).fetchone()
            if old is None or event["revision"] > old[0]:
                # Add your idempotent analysis writes here; edits replace old contributions.
                # Also retract old contributions when a usable_for_* flag becomes false.
                if on_upsert:
                    on_upsert(db, json.loads(old[1]) if old else None, record)
                db.execute("INSERT INTO reviews VALUES(?,?,?) ON CONFLICT(review_id) DO UPDATE SET revision=excluded.revision,record_json=excluded.record_json",
                           (event["review_id"], event["revision"], json.dumps(record, ensure_ascii=False)))
                changed += 1
        if batch["has_more"] and next_cursor == cursor:
            raise ValueError("API has_more without progress")
        for key, value in (("app_id", str(app_id)), ("stream_id", batch["stream_id"]), ("cursor", str(next_cursor))):
            db.execute("INSERT INTO state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    return next_cursor, changed


def _api_request(url, base_url):
    """Attach the group login when the collection API is not on localhost."""
    request = Request(url)
    user = os.environ.get("STEAM_API_USER", "")
    password = os.environ.get("STEAM_API_PASSWORD", "")
    lowered = base_url.lower()
    local = "127.0.0.1" in lowered or "localhost" in lowered
    if user and password:
        token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
        request.add_unredirected_header("Authorization", "Basic " + token)
    elif not local:
        raise RuntimeError(
            "Set STEAM_API_USER and STEAM_API_PASSWORD before reading the cloud collection API"
        )
    return request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer", default="example")
    parser.add_argument("--app-id", type=int, default=730, help="Steam App ID; default preserves the CS2 consumer path")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--snapshot", type=Path, help="Initialize an empty consumer from JSONL and its paired .manifest.json")
    parser.add_argument("--poll-seconds", type=float, default=60)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.consumer):
        parser.error("Consumer name must be 1-64 letters/digits/_/-")
    if not math.isfinite(args.poll_seconds) or args.poll_seconds <= 0:
        parser.error("poll-seconds must be finite and positive")
    try:
        output = consumer_path(args.consumer, args.app_id)
    except ValueError as error:
        parser.error(str(error))
    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as db:
        if args.reset:
            db.execute("DROP TABLE IF EXISTS reviews")
            db.execute("DROP TABLE IF EXISTS state")
        db.execute("CREATE TABLE IF NOT EXISTS reviews(review_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,record_json TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.commit()
        if args.snapshot:
            snapshot_cursor, imported = load_snapshot(db, args.snapshot, app_id=args.app_id)
            print(f"Verified snapshot: rows={imported} cursor={snapshot_cursor}; connect to the same stream", flush=True)
        row = db.execute("SELECT value FROM state WHERE key='cursor'").fetchone()
        cursor = int(row[0]) if row else 0
        print(f"App={args.app_id}; output={output}; cursor={cursor}", flush=True)
        while True:
            url = args.base_url.rstrip("/") + "/v1/reviews/changes?" + urlencode({"app_id": args.app_id, "cursor": cursor, "limit": 100})
            try:
                with urlopen(_api_request(url, args.base_url), timeout=30) as response:
                    batch = json.load(response)
                cursor, changed = save_batch(db, batch, cursor, app_id=args.app_id)
                print(f"events={len(batch['items'])} updated={changed} cursor={cursor}", flush=True)
                if batch["has_more"]:
                    continue
                if args.once:
                    return 0
            except HTTPError as exc:
                if exc.code == 409:
                    raise ValueError("Cursor is outside current stream; verify source or explicitly --reset") from exc
                if args.once:
                    raise
                print(f"HTTP {exc.code}; retaining cursor", flush=True)
            except (URLError, TimeoutError, OSError) as exc:
                if args.once:
                    raise
                print(f"Network error; retaining cursor: {exc}", flush=True)
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception as error:
        print(f"Stopped without advancing failed batch: {error}")
        raise SystemExit(1)
