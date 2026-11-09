from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .entities import EntityConfig
from .settings import (
    ALGORITHM_NAME,
    ALGORITHM_VERSION,
    CURRENT_PIPELINE,
    DASHBOARD_SCHEMA_VERSION,
    DEFAULT_ALPHA,
    DEFAULT_BASELINE_HOURS,
    DEFAULT_CHANGE_THRESHOLD,
    DEFAULT_MIN_MENTIONS,
    DEFAULT_WINDOW_HOURS,
    LLM_ALGORITHM_NAME,
    LLM_ALGORITHM_VERSION,
    LLM_PROMPT_VERSION,
    SUMMARY_SCHEMA_VERSION,
)
from .store import (
    analysis_row_count,
    analyzed_reviews,
    llm_labeled_reviews,
    llm_labeled_rows,
    scorer_counts,
    state_get,
)
from .util import atomic_write_json, iso_hour, iso_utc, parse_utc, utc_now

LABELS = ("positive", "negative", "neutral")


def empty_counts() -> dict:
    return {label: 0 for label in LABELS}


def _counts_from_rows(rows) -> dict:
    counts = empty_counts()
    for row in rows:
        label = row["sentiment_label"]
        if label in counts:
            counts[label] += int(row["n"])
    mentions = sum(counts.values())
    return {
        "positive_count": counts["positive"],
        "negative_count": counts["negative"],
        "neutral_count": counts["neutral"],
        "mention_count": mentions,
        "metric": (counts["positive"] - counts["negative"]) / mentions if mentions else None,
    }


def query_window(
    db: sqlite3.Connection,
    entity_id: str,
    start: datetime,
    end: datetime,
) -> tuple[dict, list[str]]:
    rows = db.execute(
        """
        SELECT a.sentiment_label,COUNT(*) AS n
        FROM effective_analysis a
        JOIN review_status s ON s.review_id=a.review_id AND s.revision=a.revision
        WHERE a.entity_id=? AND s.status='analyzed'
          AND a.created_at>=? AND a.created_at<?
        GROUP BY a.sentiment_label
        """,
        (entity_id, iso_utc(start), iso_utc(end)),
    ).fetchall()
    counts = _counts_from_rows(rows)
    versions = [row[0] for row in db.execute(
        """
        SELECT DISTINCT a.pipeline_version
        FROM effective_analysis a
        JOIN review_status s ON s.review_id=a.review_id AND s.revision=a.revision
        WHERE a.entity_id=? AND s.status='analyzed'
          AND a.created_at>=? AND a.created_at<?
        ORDER BY a.pipeline_version
        """,
        (entity_id, iso_utc(start), iso_utc(end)),
    ).fetchall()]
    return counts, versions


def chi_square_independence(current: dict, baseline: dict, alpha: float = DEFAULT_ALPHA) -> dict:
    observed = [
        [current["positive_count"], current["negative_count"], current["neutral_count"]],
        [baseline["positive_count"], baseline["negative_count"], baseline["neutral_count"]],
    ]
    total = sum(sum(row) for row in observed)
    if total == 0:
        return {"test": "chi_square_independence", "df": 2, "chi2": None, "p_value": None,
                "conditions_met": False, "significant": False}
    row_totals = [sum(row) for row in observed]
    col_totals = [sum(observed[r][c] for r in range(2)) for c in range(3)]
    expected = [[row_totals[r] * col_totals[c] / total for c in range(3)] for r in range(2)]
    positive_expected = [value for row in expected for value in row if value > 0]
    conditions = bool(positive_expected) and all(value >= 5 for value in positive_expected)
    chi2 = sum(
        (observed[r][c] - expected[r][c]) ** 2 / expected[r][c]
        for r in range(2)
        for c in range(3)
        if expected[r][c] > 0
    )
    p_value = math.exp(-chi2 / 2.0) if conditions else None
    return {
        "test": "chi_square_independence",
        "df": 2,
        "chi2": round(chi2, 6),
        "p_value": round(p_value, 8) if p_value is not None else None,
        "conditions_met": conditions,
        "expected_min": round(min(positive_expected), 4) if positive_expected else None,
        "significant": bool(conditions and p_value is not None and p_value < alpha),
    }


def _backfill_incomplete(health: dict) -> bool:
    status = health.get("backfill_status")
    if status in {"pending", "running"}:
        return True
    return bool(health.get("checkpoint_recent") or health.get("checkpoint_updated"))


def _source_stale(health: dict) -> bool:
    last = health.get("last_success_at")
    if not last:
        return False
    try:
        age = (utc_now() - parse_utc(last)).total_seconds()
    except ValueError:
        return False
    poll = health.get("poll_seconds")
    limit = max(1800, 3 * poll) if isinstance(poll, (int, float)) and not isinstance(poll, bool) else 1800
    return age > limit


def _data_status(
    mode: str,
    health: dict,
    window_versions: set[str],
    has_rows: bool,
    llm_coverage: float | None,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if mode == "sample":
        return "sample", ["synthetic sample data"]
    if _backfill_incomplete(health):
        reasons.append("backfill status is pending/running")
    if _source_stale(health):
        reasons.append("source has not refreshed within the expected window")
    mixed = sorted(version for version in window_versions if version != CURRENT_PIPELINE)
    if mixed:
        reasons.append("window contains non-current pipeline versions: " + ", ".join(mixed))
    if not has_rows and not reasons:
        return "no_data", ["no analyzed mentions in the window"]
    if llm_coverage is not None and llm_coverage < 0.999:
        if llm_coverage <= 0:
            reasons.append("no LLM labels imported; summaries are VADER-only")
        else:
            reasons.append(f"LLM coverage is {llm_coverage:.1%}; remaining rows use VADER fallback")
    if reasons:
        if "backfill status is pending/running" in reasons:
            return "backfill_incomplete", reasons
        if any("source has not refreshed" in reason for reason in reasons):
            return "stale_source", reasons
        if mixed:
            return "mixed_pipeline_versions", reasons
        if llm_coverage is not None and llm_coverage <= 0:
            return "vader_only", reasons
        return "llm_partial", reasons
    return "ok", []


def summarize_game(
    db: sqlite3.Connection,
    config: EntityConfig,
    health: dict,
    *,
    now: datetime | None = None,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    baseline_hours: float = DEFAULT_BASELINE_HOURS,
    min_mentions: int = DEFAULT_MIN_MENTIONS,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    alpha: float = DEFAULT_ALPHA,
) -> dict:
    now = now or utc_now()
    current_start = now - timedelta(hours=window_hours)
    baseline_start = current_start - timedelta(hours=baseline_hours)
    mode = state_get(db, "mode", "live") or "live"
    window_versions: set[str] = set()
    entities_out = []
    has_rows = False
    for entity in config.entities:
        current, current_versions = query_window(db, entity.entity_id, current_start, now)
        baseline, baseline_versions = query_window(db, entity.entity_id, baseline_start, current_start)
        window_versions.update(current_versions)
        window_versions.update(baseline_versions)
        if current["mention_count"] or baseline["mention_count"]:
            has_rows = True
        change = None
        if current["metric"] is not None and baseline["metric"] is not None:
            change = current["metric"] - baseline["metric"]
        if current["mention_count"] < min_mentions:
            entity_status = "insufficient_window"
        elif baseline["mention_count"] < min_mentions:
            entity_status = "insufficient_baseline"
        else:
            entity_status = "ok"
        change_flag = bool(
            entity_status == "ok" and change is not None and abs(change) >= change_threshold
        )
        test = chi_square_independence(current, baseline, alpha)
        if test["significant"]:
            alert_type = "statistical"
        elif change_flag:
            alert_type = "threshold"
        else:
            alert_type = "none"
        entities_out.append({
            "entity_id": entity.entity_id,
            "name": entity.name,
            "scope": entity.scope,
            "mention_required": entity.require_mention,
            "current": current,
            "baseline": baseline,
            "metric": "net_sentiment",
            "change": round(change, 6) if change is not None else None,
            "change_flag": change_flag,
            "alert_type": alert_type,
            "significance": test,
            "data_status": entity_status,
            "provisional": False,
            "current_pipeline_versions": current_versions,
            "baseline_pipeline_versions": baseline_versions,
        })
    analyzed = analyzed_reviews(db)
    llm_labeled = llm_labeled_reviews(db)
    llm_rows = llm_labeled_rows(db)
    vader_rows_total = analysis_row_count(db)
    llm_coverage = (llm_rows / vader_rows_total) if vader_rows_total else None
    scorers = scorer_counts(db)
    data_status, reasons = _data_status(mode, health, window_versions, has_rows, llm_coverage)
    provisional = data_status != "ok"
    for item in entities_out:
        item["provisional"] = provisional
    status_counts = {row["status"]: int(row["n"]) for row in db.execute(
        "SELECT status,COUNT(*) AS n FROM review_status GROUP BY status"
    ).fetchall()}
    total_reviews = db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    processed = state_get(db, "cursor")
    latest = health.get("latest_cursor")
    lag = None
    try:
        if processed is not None and latest is not None:
            lag = int(latest) - int(processed)
    except (TypeError, ValueError):
        lag = None
    generated = iso_utc(utc_now())
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "app_id": config.app_id,
        "game": config.game,
        "consumer": state_get(db, "consumer"),
        "generated_at": generated,
        "computed_at": generated,
        "mode": mode,
        "source_mode": health.get("mode"),
        "dataset_type": health.get("dataset_type"),
        "algorithm": {
            "name": LLM_ALGORITHM_NAME,
            "version": LLM_ALGORITHM_VERSION,
            "vader_fallback_version": ALGORITHM_VERSION,
            "llm_model": state_get(db, "llm_model") or "CS5351/DeepSeek-V4-Flash",
            "llm_prompt_version": state_get(db, "llm_prompt_version") or LLM_PROMPT_VERSION,
            "entity_config_version": config.version,
            "entity_config_hash": config.content_hash,
            "window_hours": window_hours,
            "baseline_hours": baseline_hours,
            "min_mentions": min_mentions,
            "change_threshold": change_threshold,
            "alpha": alpha,
            "label_thresholds": {"positive": 0.05, "negative": -0.05},
            "significance_test": "chi_square_independence_df2",
        },
        "window": {
            "current_start": iso_utc(current_start),
            "current_end": iso_utc(now),
            "baseline_start": iso_utc(baseline_start),
            "baseline_end": iso_utc(current_start),
        },
        "source_stream_id": state_get(db, "stream_id"),
        "processed_cursor": processed,
        "source_latest_cursor": latest,
        "cursor_lag": lag,
        "health_fetched_at": state_get(db, "health_fetched_at"),
        "backfill_status": health.get("backfill_status"),
        "backfill_target_days": health.get("backfill_target_days"),
        "scope_start_unix": health.get("scope_start_unix"),
        "last_success_at": health.get("last_success_at"),
        "last_error": health.get("last_error"),
        "pipeline_versions": sorted(window_versions),
        "review_counts": {
            "total": int(total_reviews),
            "analyzed": status_counts.get("analyzed", 0),
            "llm_labeled_reviews": llm_labeled,
            "llm_labeled_rows": llm_rows,
            "vader_rows": vader_rows_total,
            "excluded_unusable": status_counts.get("excluded_unusable", 0),
            "skipped_empty": status_counts.get("skipped_empty", 0),
        },
        "llm_coverage": llm_coverage,
        "scorer_counts": scorers,
        "vader_fallback_rows": scorers.get("vader", 0),
        "data_status": data_status,
        "data_status_reasons": reasons,
        "provisional": provisional,
        "entities": entities_out,
    }


def write_summary(summary: dict, directory: Path) -> tuple[Path, Path]:
    directory = Path(directory)
    stamp = summary["generated_at"].replace(":", "").replace("-", "")
    history = directory / f"summary_{stamp}.json"
    latest = directory / "latest.json"
    atomic_write_json(history, summary)
    atomic_write_json(latest, summary)
    return latest, history


def export_dashboard(
    db: sqlite3.Connection,
    config: EntityConfig,
    *,
    now: datetime | None = None,
    days: int = 7,
    bucket_hours: int = 1,
    min_mentions: int = DEFAULT_MIN_MENTIONS,
) -> dict:
    now = now or utc_now()
    if days <= 0 or bucket_hours <= 0:
        raise ValueError("days and bucket_hours must be positive")
    end = now.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=days * 24)
    bucket_delta = timedelta(hours=bucket_hours)
    buckets = []
    cursor = start
    while cursor < end:
        buckets.append(cursor)
        cursor += bucket_delta
    index = {bucket: position for position, bucket in enumerate(buckets)}
    grouped: dict[str, list[dict]] = {
        entity.entity_id: [empty_counts() for _ in buckets] for entity in config.entities
    }
    rows = db.execute(
        """
        SELECT a.entity_id,a.created_at,a.sentiment_label
        FROM effective_analysis a
        JOIN review_status s ON s.review_id=a.review_id AND s.revision=a.revision
        WHERE s.status='analyzed'
          AND a.created_at>=? AND a.created_at<?
        """,
        (iso_utc(start), iso_utc(end)),
    ).fetchall()
    for row in rows:
        if row["entity_id"] not in grouped:
            continue
        created = parse_utc(row["created_at"])
        bucket = created.replace(minute=0, second=0, microsecond=0)
        if bucket_hours > 1:
            offset_hours = int((bucket - start).total_seconds() // 3600)
            bucket = start + timedelta(hours=(offset_hours // bucket_hours) * bucket_hours)
        position = index.get(bucket)
        if position is None:
            continue
        if row["sentiment_label"] in grouped[row["entity_id"]][position]:
            grouped[row["entity_id"]][position][row["sentiment_label"]] += 1
    entities_out = []
    for entity in config.entities:
        entity_buckets = []
        for position, bucket in enumerate(buckets):
            counts = grouped[entity.entity_id][position]
            mentions = sum(counts.values())
            bucket_end = bucket + bucket_delta
            entity_buckets.append({
                "bucket_start": iso_hour(bucket),
                "bucket_end": iso_hour(min(bucket_end, end)),
                "positive_count": counts["positive"],
                "negative_count": counts["negative"],
                "neutral_count": counts["neutral"],
                "mention_count": mentions,
                "net_metric": (counts["positive"] - counts["negative"]) / mentions if mentions else None,
                "data_status": "ok" if mentions >= min_mentions else ("no_data" if mentions == 0 else "insufficient_sample"),
            })
        entities_out.append({
            "entity_id": entity.entity_id,
            "name": entity.name,
            "buckets": entity_buckets,
        })
    return {
        "schema_version": DASHBOARD_SCHEMA_VERSION,
        "app_id": config.app_id,
        "game": config.game,
        "generated_at": iso_utc(utc_now()),
        "bucket_hours": bucket_hours,
        "min_mentions": min_mentions,
        "range": {"start": iso_utc(start), "end": iso_utc(end)},
        "algorithm_version": LLM_ALGORITHM_VERSION,
        "entities": entities_out,
    }
