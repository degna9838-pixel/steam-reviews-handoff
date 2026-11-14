
"""Check consumer progress against the upstream Steam review API."""

import re
import sqlite3

from contextlib import closing

import httpx

from .cloud import require_authorization
from .storage import get_database_path


def get_consumer_state(
    app_id,
    consumer_name="trend_demo",
    data_root=None,
):
    """Read saved synchronization state from the consumer SQLite DB."""

    path = get_database_path(
        app_id=app_id,
        consumer_name=consumer_name,
        data_root=data_root,
    )

    if not path.is_file():
        raise FileNotFoundError(f"Consumer database not found: {path}")

    uri = path.resolve().as_uri() + "?mode=ro"

    with closing(sqlite3.connect(uri, uri=True)) as db:
        state = dict(
            db.execute("SELECT key, value FROM state")
        )

    if state.get("app_id") != str(app_id):
        raise ValueError("Consumer state has an unexpected app_id")

    stream_id = state.get("stream_id")
    cursor = state.get("cursor")

    if not isinstance(stream_id, str) or not stream_id:
        raise ValueError("Missing consumer stream_id")

    if not isinstance(cursor, str) or not re.fullmatch(r"[0-9]+", cursor):
        raise ValueError("Invalid consumer cursor")

    return {
        "app_id": app_id,
        "consumer_name": consumer_name,
        "stream_id": stream_id,
        "cursor": cursor,
    }



def get_sync_status(
    app_id,
    consumer_name="trend_demo",
    base_url="http://127.0.0.1:8000",
    data_root=None,
):
    """
    Compare the local consumer progress with upstream API health.

    A completed backfill and a caught-up cursor indicate readiness
    for provisional analysis, not proof of exhaustive data coverage.
    """
    local = get_consumer_state(
        app_id=app_id,
        consumer_name=consumer_name,
        data_root=data_root,
    )

    header = require_authorization(base_url)
    response = httpx.get(
        f"{base_url.rstrip('/')}/v1/health",
        params={"app_id": app_id},
        headers={"Authorization": header} if header else None,
        timeout=10.0,
        trust_env=False,
    )
    response.raise_for_status()
    health = response.json()

    if health.get("app_id") != app_id:
        raise ValueError("Unexpected app_id in upstream health")

    upstream_stream = health.get("stream_id")
    latest_cursor = health.get("latest_cursor")

    if not isinstance(upstream_stream, str) or not upstream_stream:
        raise ValueError("Missing upstream stream_id")

    if (
        isinstance(latest_cursor, bool)
        or not re.fullmatch(r"[0-9]+", str(latest_cursor))
    ):
        raise ValueError("Invalid upstream latest_cursor")

    local_cursor_num = int(local["cursor"])
    upstream_cursor_num = int(latest_cursor)

    stream_matches = local["stream_id"] == upstream_stream
    caught_up = (
        stream_matches
        and local_cursor_num == upstream_cursor_num
    )

    mode = health.get("mode")
    data_ready = health.get("data_ready") is True
    cleaning_ready = health.get("cleaning_ready") is True
    collector_state = health.get("collector_state")
    backfill_status = health.get("backfill_status")

    # Report what the upstream collector claims, without
    # treating it as independent proof of full completeness.
    backfill_reported_complete = (
        mode == "live"
        and backfill_status == "completed"
    )

    if not stream_matches:
        sync_status = "stream_mismatch"

    elif local_cursor_num > upstream_cursor_num:
        sync_status = "cursor_ahead_of_upstream"

    elif not caught_up:
        sync_status = "cursor_not_caught_up"

    elif mode == "offline":
        sync_status = "offline_sample"

    elif mode != "live":
        sync_status = "unknown_source_mode"

    elif not data_ready or not cleaning_ready:
        sync_status = "upstream_not_ready"

    elif collector_state != "running":
        sync_status = "collector_not_running"

    elif backfill_status == "running":
        sync_status = "backfill_in_progress"

    elif not backfill_reported_complete:
        sync_status = "backfill_not_verified"

    else:
        sync_status = "live_synced_backfill_reported_complete"

    provisional_ready = (
        sync_status == "live_synced_backfill_reported_complete"
    )

    return {
        "app_id": app_id,
        "consumer_name": consumer_name,
        "local_cursor": local["cursor"],
        "upstream_latest_cursor": str(latest_cursor),
        "consumer_lag_events": max(
            upstream_cursor_num - local_cursor_num, 0
        ),
        "local_stream_id": local["stream_id"],
        "upstream_stream_id": upstream_stream,
        "stream_matches": stream_matches,
        "caught_up": caught_up,
        "sync_status": sync_status,
        "mode": mode,
        "dataset_type": health.get("dataset_type"),
        "data_ready": data_ready,
        "cleaning_ready": cleaning_ready,
        "collector_state": collector_state,
        "backfill_status": backfill_status,
        "backfill_reported_complete": backfill_reported_complete,
        "ready_for_provisional_analysis": provisional_ready,
        "historical_completeness_verified": False,
        "backfill_target_days": health.get("backfill_target_days"),
        "scope_start_unix": health.get("scope_start_unix"),
        "last_error": health.get("last_error"),
        "pipeline_version": health.get("pipeline_version"),
        "checked_at": health.get("checked_at"),
    }
