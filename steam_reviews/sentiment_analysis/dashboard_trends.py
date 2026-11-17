"""Adapt shared keyword algorithms to the dashboard's current SQLite snapshot."""
from datetime import timedelta

from fastapi import HTTPException

from trend_analysis.detector import detect_from_windows
from trend_analysis.frequency import calculate_frequency

from .util import iso_utc


def dashboard_keywords(current_reviews, start, end, *, source_ready):
    """Frequent terms for the selected range, independent of surge detection."""
    pending = {"status": "pending", "eligible_count": None, "items": []}
    if not source_ready:
        return pending
    records = [record for record, created in current_reviews if start <= created < end]
    if any(type(record.get("usable_for_trend")) is not bool for record in records):
        return pending
    try:
        result = calculate_frequency(records, top_n=50)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"Invalid keyword input: {exc}") from exc
    return {"status": "ready", "eligible_count": result["total_reviews"],
            "items": [{"term": row["term"], "review_count": row["df"]} for row in result["keywords"]]}


def dashboard_trends(current_reviews, app_id, as_of, *, source_ready):
    """Use the exact records/time boundary already used by sentiment totals.

    No independent consumer, stale JSON export, network request, or LLM call is
    needed. Demo snapshots may be scored locally and remain labelled demo by
    the overview; live snapshots must pass the caller's source coverage gate.
    """
    pending = {"status": "pending", "recent_eligible_count": None,
               "baseline_eligible_count": None, "items": []}
    if not source_ready:
        return pending
    recent_start = as_of - timedelta(hours=1)
    baseline_start = as_of - timedelta(hours=25)
    reviews = [(record, created) for record, created in current_reviews
               if baseline_start <= created < as_of]
    # Legacy sentiment mirrors may predate trend fields. Unknown is not zero.
    if any(type(record.get("usable_for_trend")) is not bool for record, _ in reviews):
        return pending

    def window(start, end):
        records = [record for record, created in reviews if start <= created < end]
        try:
            frequency = calculate_frequency(records, top_n=None)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=f"Invalid trend input: {exc}") from exc
        return {"app_id": app_id, "start_time": iso_utc(start), "end_time": iso_utc(end),
                "stored_reviews_in_window": len(records), **frequency}

    result = detect_from_windows(window(recent_start, as_of), window(baseline_start, recent_start),
                                 computed_at=as_of)
    ready = result["data_status"] == "ready"
    return {
        "status": "ready" if ready else "insufficient_data",
        "recent_eligible_count": result["recent_reviews"],
        "baseline_eligible_count": result["baseline_reviews"],
        "items": [{"term": item["term"], "recent_review_count": item["recent_count"],
                   "baseline_review_count": item["baseline_count"]}
                  for item in result["trends"] if item["is_trending"]][:10],
        "candidates": [{"term": item["term"], "recent_review_count": item["recent_count"],
                        "baseline_review_count": item["baseline_count"]}
                       for item in sorted(result["trends"], key=lambda row: (-row["recent_count"], row["term"]))][:10],
    }
