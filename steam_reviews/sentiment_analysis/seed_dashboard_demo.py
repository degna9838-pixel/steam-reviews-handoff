"""Generate clearly labelled local dashboard fixtures through the real scorer.

Run from steam_reviews: python -m sentiment_analysis.seed_dashboard_demo
This never contacts Steam or an LLM and never writes a live consumer database.
"""
import argparse
import json
from datetime import timedelta
from functools import lru_cache

from cs2_reviews.cleaning import clean_review_text

from .entities import load_entity_config
from .sentiment import SentimentAnalyzer
from .settings import ALGORITHM_VERSION, SUPPORTED_APP_IDS, analysis_db_path, validate_consumer_name
from .store import (bind_state, connect, init_schema, record_algorithm_binding,
                    record_health, save_batch, state_get)
from .util import iso_utc, utc_now

CONSUMER = "dashboard_fixture_demo"
TOPICS = {
    730: ("crash", "server lag", "anticheat", "matchmaking"),
    570: ("server lag", "crash", "matchmaking", "smurf"),
    4115450: ("stutter", "camera", "performance", "combat"),
}
POSITIVE = (
    "I love this wonderful game. The controls are excellent and the experience is amazing.",
    "The graphics look beautiful and the gameplay is fantastic. I am very happy with it.",
    "Playing with friends is delightful. This is an excellent and enjoyable experience.",
    "Great controls and brilliant visuals make this a wonderful game that I really enjoy.",
)
NEGATIVE = (
    "This game is boring and disappointing. I dislike the experience and regret playing.",
    "I hate the terrible design. It feels awful and frustrating to play.",
    "The experience is bad and unpleasant. I am unhappy and very disappointed.",
)
NEUTRAL = (
    "The options menu contains settings for resolution, audio, keyboard and subtitles.",
    "I played two rounds on this computer and then returned to the main menu.",
    "The download uses disk space. My computer runs this title in a window.",
)


@lru_cache(maxsize=128)
def cleaned(text):
    return clean_review_text(text, api_language="english")


def fixture_records(app_id, as_of):
    """24 baseline hours, a negative spike, and sparse older history for 7d views."""
    rows = []

    def add(text, created, recommended):
        raw = "[DEMO REVIEW] " + text
        content = cleaned(raw)
        stamp = iso_utc(created)
        rows.append({
            "schema_version": "1.0", "source": "synthetic_dashboard_fixture",
            "dataset_type": "synthetic_examples_not_real_steam_reviews", "app_id": app_id,
            "review_id": str(700000000000 + app_id * 10000 + len(rows)),
            "revision": 1, "created_at": stamp, "updated_at": stamp,
            "first_seen_at": stamp, "last_seen_at": stamp,
            "language": "english", "steam_recommended": recommended,
            "votes_up": 0, "comment_count": 0, **content,
        })

    # The preceding 24 hours have a lower negative share and no recent incident keywords.
    for hour in range(2, 26):
        for index in range(12):
            kind = (index + hour) % 12
            if kind < 7:
                text, recommended = POSITIVE[(index + hour) % len(POSITIVE)], True
            elif kind < 10:
                text, recommended = NEGATIVE[(index + hour) % len(NEGATIVE)], False
            else:
                text, recommended = NEUTRAL[index % len(NEUTRAL)], True
            add(text, as_of - timedelta(hours=hour) + timedelta(minutes=2 + index * 4), recommended)

    for index in range(60):
        kind = (index * 17) % 60
        if kind < 48:
            topic_index = 0 if kind < 24 else (1 if kind < 36 else (2 if kind < 44 else 3))
            topic = TOPICS[app_id][topic_index]
            templates = (
                f"The {topic} issue makes this game unplayable. It is awful and disappointing.",
                f"I hate the {topic} problem. This experience is terrible and frustrating.",
                f"Repeated {topic} has ruined my matches. This is broken and unacceptable.",
                f"The game suffers from {topic}. I am unhappy with this bad experience.",
            )
            text = templates[index % len(templates)]
            recommended = False
        elif kind < 56:
            text, recommended = POSITIVE[index % len(POSITIVE)], True
        else:
            text, recommended = NEUTRAL[index % len(NEUTRAL)], True
        add(text, as_of - timedelta(hours=1) + timedelta(seconds=30 + index * 59), recommended)

    for day in range(2, 8):
        for index in range(4):
            add(POSITIVE[index], as_of - timedelta(days=day) + timedelta(hours=index + 1), True)
    return rows


def seed_game(app_id, consumer, as_of, analyzer):
    path = analysis_db_path(app_id, consumer)
    db = connect(path)
    try:
        has_state = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='state'").fetchone()
        if has_state and state_get(db, "mode") not in {None, "sample"}:
            raise ValueError("Refusing to modify a live consumer")
        init_schema(db)
        stream = f"synthetic-dashboard-fixture-v1-{app_id}"
        bind_state(db, app_id, stream, "sample")
        config = load_entity_config(app_id)
        record_algorithm_binding(db, config, ALGORITHM_VERSION, analyzer.lexicon_hash)
        db.commit()
        cursor = int(state_get(db, "cursor", "0"))
        records = fixture_records(app_id, as_of)
        for item in records:
            old = db.execute("SELECT revision FROM reviews WHERE review_id=?", (item["review_id"],)).fetchone()
            item["revision"] = old[0] + 1 if old else 1
        events = [{"change_id": cursor + index, "operation": "upsert", "review_id": item["review_id"],
                   "revision": item["revision"], "review": item}
                  for index, item in enumerate(records, start=1)]
        payload = {"schema_version": "1.0", "app_id": app_id, "stream_id": stream,
                   "items": events, "next_cursor": str(cursor + len(events)), "has_more": False}
        cursor, changed = save_batch(db, payload, cursor, app_id=app_id, config=config,
                                     analyzer=analyzer, algorithm_version=ALGORITHM_VERSION)
        record_health(db, {
            "app_id": app_id, "mode": "offline", "stream_id": stream,
            "dataset_type": "synthetic_examples_not_real_steam_reviews", "latest_cursor": str(cursor),
            "data_ready": True, "cleaning_ready": True, "backfill_status": "not_applicable",
            "scope_start_unix": int((as_of - timedelta(days=7)).timestamp()),
            "last_success_at": None, "checked_at": iso_utc(utc_now()), "poll_seconds": 600,
        }, "sample")
        return {"app_id": app_id, "mode": "demo", "consumer": consumer, "records": len(records),
                "updated": changed, "as_of": iso_utc(as_of)}
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer", default=CONSUMER)
    args = parser.parse_args()
    validate_consumer_name(args.consumer)
    if "demo" not in args.consumer:
        parser.error("The fixture consumer name must contain demo")
    as_of = utc_now().replace(minute=0, second=0, microsecond=0)
    analyzer = SentimentAnalyzer()
    for app_id in SUPPORTED_APP_IDS:
        print(json.dumps(seed_game(app_id, args.consumer, as_of, analyzer)), flush=True)
    print("Synthetic comments only. Serve with --consumer " + args.consumer + ' --collector-url ""')


if __name__ == "__main__":
    main()
