from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .aggregate import export_dashboard, summarize_game
from .entities import load_entity_config
from .settings import (
    GAME_NAMES,
    LIVE_CONSUMER,
    SUPPORTED_APP_IDS,
    analysis_db_path,
    validate_app_id,
)
from .store import (
    analysis_row_count,
    analyzed_reviews,
    get_health,
    llm_labeled_reviews,
    llm_labeled_rows,
    scorer_counts,
    state_get,
)
from .util import iso_utc, parse_utc, utc_now

OVERVIEW_SCHEMA_VERSION = "1.0"
OVERVIEW_HOURS = {6, 24, 168}
ALERT_MIN_CLASSIFIED = 20
ALERT_MIN_INCREASE_PP = 15
LABELS = ("positive", "neutral", "negative")


def connect_readonly(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def _check_app(app_id: int) -> None:
    try:
        validate_app_id(app_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _parse_now(value: str | None):
    if value is None:
        return None
    try:
        return parse_utc(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid now timestamp: {exc}") from exc


def _open(app_id: int, consumer: str) -> sqlite3.Connection:
    path = analysis_db_path(app_id, consumer)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"analysis database not found for app_id={app_id}")
    db = connect_readonly(path)
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name IN ('analysis','llm_analysis','effective_analysis')"
    ).fetchall()
    if len({item[0] for item in row}) < 3:
        db.close()
        raise HTTPException(status_code=503, detail="analysis database is not initialized; run the sentiment consumer first")
    return db


def _source_health(app_id: int, collector_url: str | None) -> dict | None:
    if not collector_url:
        return None
    try:
        with urllib.request.urlopen(
            f"{collector_url.rstrip('/')}/v1/health?app_id={app_id}", timeout=3
        ) as response:
            payload = json.load(response)
        return payload if isinstance(payload, dict) else None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def _cursor_lag(processed: str | None, latest: str | None) -> int | None:
    try:
        if processed is not None and latest is not None:
            return int(latest) - int(processed)
    except (TypeError, ValueError):
        return None
    return None


def _status(mode: str | None, health: dict, coverage: float | None) -> str:
    if mode == "sample":
        return "sample"
    last = health.get("last_success_at")
    if last:
        try:
            age = (utc_now() - parse_utc(last)).total_seconds()
            poll = health.get("poll_seconds", 600)
            limit = max(1800, 3 * poll) if isinstance(poll, (int, float)) and not isinstance(poll, bool) else 1800
            if age > limit:
                return "stale_source"
        except ValueError:
            pass
    if health.get("backfill_status") in {"pending", "running"} or health.get("checkpoint_recent") or health.get("checkpoint_updated"):
        return "backfill_incomplete"
    if coverage is None or coverage <= 0:
        return "vader_only"
    if coverage < 0.999:
        return "llm_partial"
    return "ok"


def _health_payload(db: sqlite3.Connection, app_id: int, consumer: str, collector_url: str | None) -> dict:
    cached, cached_at = get_health(db)
    source = _source_health(app_id, collector_url)
    health = source or cached or {}
    analyzed = analyzed_reviews(db)
    llm_reviews = llm_labeled_reviews(db)
    analysis_rows = analysis_row_count(db)
    llm_rows = llm_labeled_rows(db)
    coverage = (llm_rows / analysis_rows) if analysis_rows else None
    processed = state_get(db, "cursor")
    latest = health.get("latest_cursor")
    mode = state_get(db, "mode")
    return {
        "app_id": app_id,
        "game": GAME_NAMES.get(app_id, health.get("game", {}).get("name") if isinstance(health.get("game"), dict) else None),
        "consumer": consumer,
        "mode": mode,
        "source_mode": health.get("mode"),
        "prompt_version": state_get(db, "llm_prompt_version"),
        "llm_model": state_get(db, "llm_model"),
        "analyzed_reviews": analyzed,
        "llm_labeled_reviews": llm_reviews,
        "analysis_rows": analysis_rows,
        "llm_rows": llm_rows,
        "pending_llm_rows": max(0, analysis_rows - llm_rows),
        "llm_coverage": coverage,
        "scorer_counts": scorer_counts(db),
        "processed_cursor": processed,
        "source_latest_cursor": latest,
        "cursor_lag": _cursor_lag(processed, latest),
        "backfill_status": health.get("backfill_status"),
        "last_success_at": health.get("last_success_at"),
        "last_error": health.get("last_error"),
        "health_source": "collector" if source else "cached",
        "health_fetched_at": state_get(db, "health_fetched_at") if not source else utc_now().isoformat().replace("+00:00", "Z"),
        "data_status": _status(mode, health, coverage),
    }


def _dashboard_overview(
    db: sqlite3.Connection,
    app_id: int,
    health: dict,
    *,
    hours: int,
    generated_at: datetime,
) -> dict:
    as_of = generated_at.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    range_start = as_of - timedelta(hours=hours)
    recent_start = as_of - timedelta(hours=1)
    baseline_start = as_of - timedelta(hours=25)
    range_text = (iso_utc(range_start), iso_utc(as_of))
    recent_text = (iso_utc(recent_start), iso_utc(as_of))
    baseline_text = (iso_utc(baseline_start), iso_utc(recent_start))

    bound_app = state_get(db, "app_id")
    if bound_app is not None and bound_app != str(app_id):
        raise HTTPException(status_code=503, detail="Analysis database app_id mismatch")

    current_reviews: list[tuple[dict, datetime]] = []
    for row in db.execute("SELECT record_json FROM reviews"):
        try:
            record = json.loads(row["record_json"])
            if not isinstance(record, dict):
                raise ValueError("Stored review must be a JSON object")
            if type(record.get("app_id")) is not int or record["app_id"] != app_id:
                raise ValueError("Stored review app_id mismatch")
            if not isinstance(record.get("review_id"), str) or not record["review_id"]:
                raise ValueError("Stored review has invalid review_id")
            if not isinstance(record.get("text_raw"), str):
                raise ValueError("Stored review has invalid text_raw")
            if not isinstance(record.get("text_clean"), str):
                raise ValueError("Stored review has invalid text_clean")
            created_at = parse_utc(record["created_at"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=503, detail=f"Invalid review mirror record: {exc}") from exc
        if baseline_start <= created_at < as_of:
            current_reviews.append((record, created_at))

    sentiment_by_review: dict[str, str] = {}
    rows = db.execute(
        """
        SELECT e.review_id,e.sentiment_label
        FROM effective_analysis AS e
        JOIN review_status AS s
          ON s.review_id=e.review_id AND s.revision=e.revision
        JOIN reviews AS r
          ON r.review_id=e.review_id
         AND CAST(json_extract(r.record_json, '$.revision') AS INTEGER)=e.revision
        WHERE e.entity_id='game' AND s.status='analyzed'
        """
    )
    for row in rows:
        if row["sentiment_label"] in LABELS:
            sentiment_by_review[row["review_id"]] = row["sentiment_label"]

    hour_starts = [range_start + timedelta(hours=index) for index in range(hours)]
    bucket_rows: list[dict] = []
    per_bucket: list[dict[str, int]] = []
    for bucket_start in hour_starts:
        bucket_end = bucket_start + timedelta(hours=1)
        bucket_reviews = [
            record for record, created_at in current_reviews
            if bucket_start <= created_at < bucket_end
        ]
        counts = {
            "review_count": len(bucket_reviews),
            "classified_count": 0,
            "positive_count": 0,
            "neutral_count": 0,
            "negative_count": 0,
        }
        for record in bucket_reviews:
            label = sentiment_by_review.get(record["review_id"])
            if label in LABELS:
                counts["classified_count"] += 1
                counts[f"{label}_count"] += 1
        per_bucket.append(counts)
        bucket_rows.append({
            "start_at": iso_utc(bucket_start),
            "end_at": iso_utc(bucket_end),
            "counts": counts,
        })

    summary = {
        key: sum(counts[key] for counts in per_bucket)
        for key in ("review_count", "classified_count", "positive_count", "neutral_count", "negative_count")
    }

    def count_window(start: datetime, end: datetime) -> dict[str, int]:
        result = {"classified_count": 0, "negative_count": 0}
        for record, created_at in current_reviews:
            if start <= created_at < end:
                label = sentiment_by_review.get(record["review_id"])
                if label in LABELS:
                    result["classified_count"] += 1
                    if label == "negative":
                        result["negative_count"] += 1
        return result

    recent_counts = count_window(recent_start, as_of)
    baseline_counts = count_window(baseline_start, recent_start)
    mode_value = state_get(db, "mode") or health.get("mode")
    mode = "demo" if mode_value in {"sample", "offline", "demo"} else "live"
    latest_cursor = health.get("latest_cursor")
    processed_cursor = state_get(db, "cursor")
    cursor_lag = _cursor_lag(processed_cursor, latest_cursor)
    backfill = health.get("backfill_status")
    backfill_complete = backfill in {"completed", "complete", "not_applicable"}
    last_collection_at = health.get("last_success_at")
    stale = False
    if isinstance(last_collection_at, str):
        try:
            age_seconds = (generated_at.astimezone(timezone.utc) - parse_utc(last_collection_at)).total_seconds()
            interval = health.get("poll_seconds", 600)
            stale_after = max(1800, 3 * interval) if isinstance(interval, (int, float)) else 1800
            stale = age_seconds > stale_after
        except (TypeError, ValueError):
            stale = True
    eligible_count = sum(
        bool(record.get("usable_for_sentiment")) and bool(record.get("text_clean", "").strip())
        for record, created_at in current_reviews
        if range_start <= created_at < as_of
    )
    source_has_data = bool(health.get("data_ready")) or bool(current_reviews)
    analysis_updated_row = db.execute("SELECT MAX(updated_at) FROM review_status").fetchone()
    analysis_updated_at = analysis_updated_row[0] if analysis_updated_row else None
    classified_count = summary["classified_count"]
    covered_from = None
    scope_start = health.get("scope_start_unix")
    if isinstance(scope_start, (int, float)) and not isinstance(scope_start, bool):
        try:
            covered_from = datetime.fromtimestamp(scope_start, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="Invalid collector scope_start_unix") from exc
    range_partially_uncovered = covered_from is not None and covered_from > range_start
    comparison_partially_uncovered = covered_from is not None and covered_from > baseline_start
    collection_time_unknown = not isinstance(last_collection_at, str)

    if not source_has_data:
        data_state = "pending"
    elif (
        not backfill_complete
        or (cursor_lag is not None and cursor_lag > 0)
        or stale
        or classified_count < eligible_count
        or range_partially_uncovered
        or comparison_partially_uncovered
        or collection_time_unknown
    ):
        data_state = "partial"
    else:
        data_state = "ready"

    if cursor_lag is not None and cursor_lag > 0:
        for bucket in bucket_rows:
            bucket["counts"] = None
        summary_payload = None
    elif covered_from is not None and covered_from > range_start:
        for bucket, bucket_start in zip(bucket_rows, hour_starts):
            if bucket_start < covered_from:
                bucket["counts"] = None
        summary_payload = (
            None if any(bucket["counts"] is None for bucket in bucket_rows) else summary
        )
    else:
        summary_payload = summary

    if data_state == "pending":
        alert_status = "pending"
    elif data_state != "ready":
        alert_status = "pending"
    elif (
        recent_counts["classified_count"] < ALERT_MIN_CLASSIFIED
        or baseline_counts["classified_count"] < ALERT_MIN_CLASSIFIED
    ):
        alert_status = "insufficient_data"
    else:
        recent_rate = recent_counts["negative_count"] / recent_counts["classified_count"]
        baseline_rate = baseline_counts["negative_count"] / baseline_counts["classified_count"]
        increase_pp = (recent_rate - baseline_rate) * 100
        alert_status = "triggered" if increase_pp >= ALERT_MIN_INCREASE_PP else "clear"

    review_samples = []
    for record, created_at in sorted(
        (item for item in current_reviews if recent_start <= item[1] < as_of),
        key=lambda item: (item[1], item[0]["review_id"]),
        reverse=True,
    )[:5]:
        review_samples.append({
            "app_id": app_id,
            "review_id": record["review_id"],
            "created_at": iso_utc(created_at),
            "text_raw": record.get("text_raw", ""),
            "sentiment": sentiment_by_review.get(record["review_id"]),
            "steam_recommended": record.get("steam_recommended"),
        })

    return {
        "schema_version": OVERVIEW_SCHEMA_VERSION,
        "app_id": app_id,
        "as_of": iso_utc(as_of),
        "range": {"start_at": range_text[0], "end_at": range_text[1]},
        "status": {
            "mode": mode,
            "data_state": data_state,
            "last_collection_at": last_collection_at,
            "analysis_updated_at": analysis_updated_at,
            "collection_interval_seconds": health.get("poll_seconds"),
        },
        "summary": summary_payload,
        "buckets": bucket_rows,
        "comparison": {
            "recent": {"start_at": recent_text[0], "end_at": recent_text[1]},
            "baseline": {"start_at": baseline_text[0], "end_at": baseline_text[1]},
        },
        "trends": {
            "status": "pending",
            "recent_eligible_count": None,
            "baseline_eligible_count": None,
            "items": [],
        },
        "alert": {
            "status": alert_status,
            "recent": recent_counts,
            "baseline": baseline_counts,
            "min_classified_per_window": ALERT_MIN_CLASSIFIED,
            "min_increase_pp": ALERT_MIN_INCREASE_PP,
        },
        "review_samples": review_samples,
    }


def create_app(consumer: str = LIVE_CONSUMER, collector_url: str | None = "http://127.0.0.1:8000") -> FastAPI:
    app = FastAPI(title="CS5481 sentiment results API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/v1/sentiment/games")
    def games():
        items = []
        for app_id in SUPPORTED_APP_IDS:
            path = analysis_db_path(app_id, consumer)
            if not path.exists():
                items.append({"app_id": app_id, "game": GAME_NAMES.get(app_id), "available": False})
                continue
            db = _open(app_id, consumer)
            try:
                info = _health_payload(db, app_id, consumer, collector_url)
                config = load_entity_config(app_id)
                info.update({"available": True, "entities": [entity.as_dict() for entity in config.entities]})
                items.append(info)
            finally:
                db.close()
        return {"schema_version": "sentiment-api-1.0", "generated_at": utc_now().isoformat().replace("+00:00", "Z"),
                "consumer": consumer, "games": items}

    @app.get("/v1/dashboard/overview")
    def dashboard_overview(
        app_id: int = Query(..., gt=0),
        hours: int = Query(24),
    ):
        _check_app(app_id)
        if hours not in OVERVIEW_HOURS:
            raise HTTPException(status_code=422, detail="hours must be one of 6, 24, or 168")
        db = _open(app_id, consumer)
        try:
            cached_health, _ = get_health(db)
            source_health = _source_health(app_id, collector_url)
            health = source_health or cached_health or {}
            return _dashboard_overview(
                db,
                app_id,
                health,
                hours=hours,
                generated_at=utc_now(),
            )
        finally:
            db.close()

    @app.get("/v1/sentiment/health")
    def health(app_id: int = Query(..., gt=0)):
        _check_app(app_id)
        db = _open(app_id, consumer)
        try:
            return {"schema_version": "sentiment-api-1.0", **_health_payload(db, app_id, consumer, collector_url)}
        finally:
            db.close()

    @app.get("/v1/sentiment/entities")
    def entities(app_id: int = Query(..., gt=0)):
        _check_app(app_id)
        config = load_entity_config(app_id)
        return {
            "schema_version": "sentiment-api-1.0",
            "app_id": app_id,
            "game": config.game,
            "config_version": config.version,
            "config_hash": config.content_hash,
            "entities": [entity.as_dict() for entity in config.entities],
        }

    @app.get("/v1/sentiment/pending")
    def pending(app_id: int = Query(..., gt=0)):
        _check_app(app_id)
        db = _open(app_id, consumer)
        try:
            info = _health_payload(db, app_id, consumer, collector_url)
            return {
                "schema_version": "sentiment-api-1.0",
                "app_id": app_id,
                "analyzed_reviews": info["analyzed_reviews"],
                "llm_labeled_reviews": info["llm_labeled_reviews"],
                "analysis_rows": info["analysis_rows"],
                "llm_rows": info["llm_rows"],
                "pending_llm_rows": info["pending_llm_rows"],
                "llm_coverage": info["llm_coverage"],
                "processed_cursor": info["processed_cursor"],
                "source_latest_cursor": info["source_latest_cursor"],
                "cursor_lag": info["cursor_lag"],
                "data_status": info["data_status"],
            }
        finally:
            db.close()

    @app.get("/v1/sentiment/summary")
    def summary(
        app_id: int = Query(..., gt=0),
        window_hours: float = Query(1.0, gt=0),
        baseline_hours: float = Query(24.0, gt=0),
        min_mentions: int = Query(5, ge=1),
        change_threshold: float = Query(0.15, ge=0),
        alpha: float = Query(0.05, gt=0, lt=1),
        scorer: Literal["llm", "effective"] = Query("llm"),
        now: str | None = Query(None),
    ):
        _check_app(app_id)
        db = _open(app_id, consumer)
        try:
            info = _health_payload(db, app_id, consumer, collector_url)
            source = _source_health(app_id, collector_url)
            cached, _ = get_health(db)
            health = source or cached or {}
            config = load_entity_config(app_id)
            result = summarize_game(
                db, config, health,
                now=_parse_now(now),
                window_hours=window_hours, baseline_hours=baseline_hours,
                min_mentions=min_mentions, change_threshold=change_threshold,
                alpha=alpha, scorer=None if scorer == "effective" else scorer,
            )
            result["pending_llm_rows"] = info["pending_llm_rows"]
            result["api_scorer"] = scorer
            return result
        finally:
            db.close()

    @app.get("/v1/sentiment/alerts")
    def alerts(
        app_id: int = Query(..., gt=0),
        window_hours: float = Query(1.0, gt=0),
        baseline_hours: float = Query(24.0, gt=0),
        min_mentions: int = Query(5, ge=1),
        change_threshold: float = Query(0.15, ge=0),
        scorer: Literal["llm", "effective"] = Query("llm"),
        now: str | None = Query(None),
    ):
        _check_app(app_id)
        db = _open(app_id, consumer)
        try:
            source = _source_health(app_id, collector_url)
            cached, _ = get_health(db)
            config = load_entity_config(app_id)
            result = summarize_game(
                db, config, source or cached or {},
                now=_parse_now(now),
                window_hours=window_hours, baseline_hours=baseline_hours,
                min_mentions=min_mentions, change_threshold=change_threshold,
                scorer=None if scorer == "effective" else scorer,
            )
            result["entities"] = [
                item for item in result["entities"]
                if item["change_flag"] or item["alert_type"] != "none"
            ]
            info = _health_payload(db, app_id, consumer, collector_url)
            result["pending_llm_rows"] = info["pending_llm_rows"]
            result["alert_count"] = len(result["entities"])
            result["api_scorer"] = scorer
            return result
        finally:
            db.close()

    @app.get("/v1/sentiment/series")
    def series(
        app_id: int = Query(..., gt=0),
        entity_id: str | None = Query(None),
        days: int = Query(7, ge=1, le=90),
        bucket_hours: int = Query(1, ge=1, le=24),
        min_mentions: int = Query(5, ge=1),
        scorer: Literal["llm", "effective"] = Query("llm"),
        now: str | None = Query(None),
    ):
        _check_app(app_id)
        db = _open(app_id, consumer)
        try:
            config = load_entity_config(app_id)
            if entity_id is not None and entity_id not in {entity.entity_id for entity in config.entities}:
                raise HTTPException(status_code=404, detail=f"unknown entity_id {entity_id!r}")
            payload = export_dashboard(
                db, config,
                now=_parse_now(now),
                days=days, bucket_hours=bucket_hours, min_mentions=min_mentions,
                scorer=None if scorer == "effective" else scorer,
            )
            if entity_id is not None:
                payload["entities"] = [item for item in payload["entities"] if item["entity_id"] == entity_id]
            info = _health_payload(db, app_id, consumer, collector_url)
            payload.update({
                "pending_llm_rows": info["pending_llm_rows"],
                "llm_coverage": info["llm_coverage"],
                "processed_cursor": info["processed_cursor"],
                "source_latest_cursor": info["source_latest_cursor"],
                "data_status": info["data_status"],
                "api_scorer": scorer,
            })
            return payload
        finally:
            db.close()

    return app
