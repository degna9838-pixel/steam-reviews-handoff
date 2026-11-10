"""Trend scores from Person 1's two keyword windows.

The score uses document frequency divided by the number of usable
reviews in that window. A 1-hour rate and a 24-hour rate can be
compared. Raw term frequency is kept in the result and is not the score.

    strength = min(max_strength,
                   (recent_rate + smoothing) / (baseline_rate + smoothing))

A term missing from the baseline is marked new. Smoothing keeps the
strength finite. ``is_trending`` is true only when the game sample is
large enough, the source status allows a conclusion, the recent
document frequency reaches the minimum, and the strength reaches the
threshold. These defaults are a documented first version, not a
significance test.
"""

from datetime import datetime, timedelta, timezone

from .storage import _to_utc, get_top_keywords


ALGORITHM_VERSION = "trend-df-rate-v1"

DEFAULT_PARAMETERS = {
    "recent_hours": 1,
    "baseline_hours": 24,
    "smoothing": 0.05,
    "min_recent_df": 3,
    "min_recent_reviews": 20,
    "min_baseline_reviews": 30,
    "strength_threshold": 2.0,
    "max_strength": 20.0,
}


def resolve_parameters(overrides=None):
    """Return a validated parameter dictionary."""
    params = dict(DEFAULT_PARAMETERS)
    if not overrides:
        return params
    unknown = set(overrides) - set(DEFAULT_PARAMETERS)
    if unknown:
        raise ValueError(f"Unknown trend parameters: {sorted(unknown)}")
    params.update(overrides)
    if params["recent_hours"] <= 0 or params["baseline_hours"] <= 0:
        raise ValueError("Window lengths must be positive")
    if params["smoothing"] <= 0:
        raise ValueError("smoothing must be positive so a zero baseline stays finite")
    if params["min_recent_df"] < 1:
        raise ValueError("min_recent_df must be at least 1")
    if params["min_recent_reviews"] < 1 or params["min_baseline_reviews"] < 1:
        raise ValueError("Minimum review counts must be at least 1")
    if params["strength_threshold"] <= 0:
        raise ValueError("strength_threshold must be positive")
    if params["max_strength"] <= 0:
        raise ValueError("max_strength must be positive")
    return params


def window_bounds(end, parameters=None):
    """Return baseline and recent bounds: [baseline_start, recent_start), [recent_start, end)."""
    params = resolve_parameters(parameters)
    end_at = _to_utc(end)
    recent_start = end_at - timedelta(hours=params["recent_hours"])
    baseline_start = recent_start - timedelta(hours=params["baseline_hours"])
    return baseline_start, recent_start, recent_start, end_at


def term_strength(recent_rate, baseline_rate, smoothing, max_strength):
    """Smoothed rate ratio, capped so a zero baseline cannot become infinite."""
    strength = (recent_rate + smoothing) / (baseline_rate + smoothing)
    return min(strength, max_strength)


def assess_data_status(recent_reviews, baseline_reviews, parameters, sync_status=None):
    """Decide whether the two windows are complete enough to publish a trend list."""
    params = resolve_parameters(parameters)
    if recent_reviews <= 0:
        sample_status = "empty_recent"
    elif recent_reviews < params["min_recent_reviews"]:
        sample_status = "sparse_recent"
    elif baseline_reviews < params["min_baseline_reviews"]:
        sample_status = "sparse_baseline"
    else:
        sample_status = "ready"

    if sync_status is None:
        return sample_status, sample_status == "ready"

    if sync_status.get("mode") == "offline":
        return "offline_sample", False
    if sync_status.get("ready_for_provisional_analysis") is not True:
        reason = sync_status.get("sync_status") or "not_ready"
        return reason, False
    if sample_status != "ready":
        return sample_status, False
    return "ready", True


def _index_keywords(payload):
    return {item["term"]: item for item in payload["keywords"]}


def _rate(count, denominator):
    if denominator <= 0:
        return 0.0
    return count / denominator


def detect_from_windows(recent, baseline, *, parameters=None, sync_status=None, computed_at=None):
    """Score one game from two ``get_top_keywords`` results.

    The baseline must end where the recent window starts. The windows
    therefore do not overlap, and they belong to the same game.
    """
    params = resolve_parameters(parameters)
    if recent["app_id"] != baseline["app_id"]:
        raise ValueError("Recent and baseline windows belong to different games")
    if recent["start_time"] != baseline["end_time"]:
        raise ValueError("Baseline must end exactly when the recent window starts")

    app_id = recent["app_id"]
    recent_reviews = recent["total_reviews"]
    baseline_reviews = baseline["total_reviews"]
    data_status, publishable = assess_data_status(
        recent_reviews, baseline_reviews, params, sync_status
    )
    stamp = computed_at or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        raise ValueError("computed_at must include a timezone")
    stamp = stamp.astimezone(timezone.utc)
    computed = stamp.isoformat()

    recent_terms = _index_keywords(recent)
    baseline_terms = _index_keywords(baseline)
    rows = []
    for term in sorted(recent_terms):
        recent_item = recent_terms[term]
        baseline_item = baseline_terms.get(term)
        recent_df = recent_item["df"]
        baseline_df = 0 if baseline_item is None else baseline_item["df"]
        recent_rate = _rate(recent_df, recent_reviews)
        baseline_rate = _rate(baseline_df, baseline_reviews)
        strength = term_strength(
            recent_rate, baseline_rate, params["smoothing"], params["max_strength"]
        )
        is_new = baseline_df == 0
        meets_support = recent_df >= params["min_recent_df"]
        meets_threshold = strength >= params["strength_threshold"]
        is_trending = publishable and meets_support and meets_threshold
        rows.append({
            "app_id": app_id,
            "term": term,
            "recent_start": recent["start_time"],
            "recent_end": recent["end_time"],
            "baseline_start": baseline["start_time"],
            "baseline_end": baseline["end_time"],
            "recent_count": recent_df,
            "baseline_count": baseline_df,
            "recent_tf": recent_item["tf"],
            "baseline_tf": 0 if baseline_item is None else baseline_item["tf"],
            "recent_denominator": recent_reviews,
            "baseline_denominator": baseline_reviews,
            "recent_rate": recent_rate,
            "baseline_rate": baseline_rate,
            "strength": round(strength, 6),
            "is_new": is_new,
            "meets_support": meets_support,
            "meets_threshold": meets_threshold,
            "is_trending": is_trending,
            "algorithm_version": ALGORITHM_VERSION,
            "computed_at": computed,
            "data_status": data_status,
            "count_definition": "document_frequency",
        })

    rows.sort(key=lambda item: (-item["strength"], -item["recent_count"], item["term"]))
    for rank, item in enumerate(rows, start=1):
        item["rank"] = rank

    stream_id = None if sync_status is None else sync_status.get("local_stream_id")
    cursor = None if sync_status is None else sync_status.get("local_cursor")
    return {
        "schema_version": "trend-result-v1",
        "algorithm_version": ALGORITHM_VERSION,
        "parameters": params,
        "app_id": app_id,
        "consumer_name": recent.get("consumer_name"),
        "computed_at": computed,
        "recent_start": recent["start_time"],
        "recent_end": recent["end_time"],
        "baseline_start": baseline["start_time"],
        "baseline_end": baseline["end_time"],
        "recent_reviews": recent_reviews,
        "baseline_reviews": baseline_reviews,
        "recent_stored_reviews": recent["stored_reviews_in_window"],
        "baseline_stored_reviews": baseline["stored_reviews_in_window"],
        "data_status": data_status,
        "conclusions_publishable": publishable,
        "historical_completeness_verified": False if sync_status is None else bool(
            sync_status.get("historical_completeness_verified")
        ),
        "source_stream_id": stream_id,
        "processed_cursor": cursor,
        "sync_status": None if sync_status is None else sync_status.get("sync_status"),
        "trends": rows,
        "trending_terms": [item["term"] for item in rows if item["is_trending"]],
    }


def detect_trends(
    app_id,
    *,
    consumer_name="trend_live",
    end=None,
    parameters=None,
    data_root=None,
    sync_status=None,
    computed_at=None,
):
    """Query Person 1 for both windows and score the terms in one game."""
    params = resolve_parameters(parameters)
    end_at = datetime.now(timezone.utc) if end is None else _to_utc(end)
    baseline_start, baseline_end, recent_start, recent_end = window_bounds(end_at, params)
    common = {
        "app_id": app_id,
        "top_n": None,
        "consumer_name": consumer_name,
        "data_root": data_root,
    }
    baseline = get_top_keywords(start_time=baseline_start, end_time=baseline_end, **common)
    recent = get_top_keywords(start_time=recent_start, end_time=recent_end, **common)
    return detect_from_windows(
        recent,
        baseline,
        parameters=params,
        sync_status=sync_status,
        computed_at=computed_at or end_at,
    )


def empty_result(app_id, *, consumer_name, data_status, message, end=None, parameters=None):
    """A game result used when the local snapshot cannot be read."""
    params = resolve_parameters(parameters)
    end_at = datetime.now(timezone.utc) if end is None else _to_utc(end)
    baseline_start, baseline_end, recent_start, recent_end = window_bounds(end_at, params)
    computed = end_at.isoformat()
    return {
        "schema_version": "trend-result-v1",
        "algorithm_version": ALGORITHM_VERSION,
        "parameters": params,
        "app_id": app_id,
        "consumer_name": consumer_name,
        "computed_at": computed,
        "recent_start": recent_start.isoformat(),
        "recent_end": recent_end.isoformat(),
        "baseline_start": baseline_start.isoformat(),
        "baseline_end": baseline_end.isoformat(),
        "recent_reviews": 0,
        "baseline_reviews": 0,
        "recent_stored_reviews": 0,
        "baseline_stored_reviews": 0,
        "data_status": data_status,
        "conclusions_publishable": False,
        "historical_completeness_verified": False,
        "source_stream_id": None,
        "processed_cursor": None,
        "sync_status": data_status,
        "message": message,
        "trends": [],
        "trending_terms": [],
    }
