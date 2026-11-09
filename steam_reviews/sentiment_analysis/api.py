from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
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
from .util import parse_utc, utc_now


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
