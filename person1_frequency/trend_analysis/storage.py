
"""Read current Steam reviews from the existing consumer SQLite databases."""

import json
import re
import sqlite3

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .frequency import calculate_frequency


SUPPORTED_APP_IDS = {730, 570, 4115450}

# The directory created by the existing API consumer.
DEFAULT_DATA_ROOT = Path(__file__).resolve().parent.parent / "data"


def _to_utc(value):
    """Convert an ISO-8601 string or aware datetime to UTC."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid ISO-8601 datetime: {value}") from exc

    if not isinstance(value, datetime):
        raise TypeError("Time must be a datetime or ISO-8601 string")

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Time must include a timezone")

    return value.astimezone(timezone.utc)


def get_database_path(app_id, consumer_name="trend_demo", data_root=None):
    """Locate the existing consumer database for one Steam game."""
    if type(app_id) is not int or app_id not in SUPPORTED_APP_IDS:
        raise ValueError(f"Unsupported app_id: {app_id}")

    if not isinstance(consumer_name, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", consumer_name
    ):
        raise ValueError("Invalid consumer_name")

    root = Path(data_root) if data_root is not None else DEFAULT_DATA_ROOT
    filename = f"consumer_{consumer_name}.sqlite3"

    if app_id == 730:
        return root / filename

    return root / "games" / str(app_id) / filename


def get_reviews(
    app_id,
    start_time,
    end_time,
    consumer_name="trend_demo",
    data_root=None,
):
    """
    Return current-version reviews for one game in [start_time, end_time).

    The time filter uses the original Steam review creation time.
    Both usable and unusable records are retained here; the frequency
    function decides which records contribute to keyword statistics.
    """
    start = _to_utc(start_time)
    end = _to_utc(end_time)

    if start >= end:
        raise ValueError("start_time must be earlier than end_time")

    db_path = get_database_path(app_id, consumer_name, data_root)

    if not db_path.is_file():
        raise FileNotFoundError(f"Consumer database not found: {db_path}")

    matched = []

    # Read-only SQLite connection: this function never modifies the DB.
    uri = db_path.resolve().as_uri() + "?mode=ro"

    with closing(sqlite3.connect(uri, uri=True)) as db:
        rows = db.execute(
            "SELECT review_id, revision, record_json FROM reviews"
        )

        for stored_review_id, stored_revision, record_json in rows:
            review = json.loads(record_json)

            if not isinstance(review, dict):
                raise ValueError("Invalid stored review JSON")

            # Detect corrupted or mismatched consumer records.
            if str(review.get("review_id")) != str(stored_review_id):
                raise ValueError("Stored review_id does not match JSON")

            if review.get("revision") != stored_revision:
                raise ValueError("Stored revision does not match JSON")

            if review.get("app_id") != app_id:
                raise ValueError("Review belongs to the wrong game")

            created_at = _to_utc(review["created_at"])

            if start <= created_at < end:
                matched.append((created_at, review))

    # Deterministic ordering helps testing and reproducibility.
    matched.sort(
        key=lambda item: (item[0], str(item[1]["review_id"]))
    )

    return [review for _, review in matched]


def get_top_keywords(
    app_id,
    start_time,
    end_time,
    top_n=20,
    consumer_name="trend_demo",
    data_root=None,
):
    """Query current reviews and calculate keyword frequency statistics."""
    reviews = get_reviews(
        app_id=app_id,
        start_time=start_time,
        end_time=end_time,
        consumer_name=consumer_name,
        data_root=data_root,
    )

    frequency = calculate_frequency(reviews, top_n=top_n)

    return {
        "app_id": app_id,
        "consumer_name": consumer_name,
        "start_time": _to_utc(start_time).isoformat(),
        "end_time": _to_utc(end_time).isoformat(),
        "stored_reviews_in_window": len(reviews),
        **frequency,
    }
