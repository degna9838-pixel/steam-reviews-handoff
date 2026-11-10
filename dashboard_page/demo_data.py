from datetime import datetime, timedelta
from random import Random

# Fixed, synthetic snapshot in UTC+8. The dashboard can run without a collector.
SNAPSHOT_AT = datetime(2026, 11, 9, 20)
TIME_RANGES = {"Last 6 hours": 6, "Last 24 hours": 24, "Last 7 days": 168}
SENTIMENTS = ("Negative", "Neutral", "Positive")

GAMES = {
    730: {
        "name": "Counter-Strike 2",
        "short": "CS2",
        "hourly_reviews": 50,
        "latest_negative_share": 0.60,
        "terms": ("anticheat", "crash", "matchmaking", "server"),
        "examples": (
            (14, "VAC needs work. I keep running into cheaters.", "Negative", False),
            (38, "I still recommend CS2, but matchmaking has been slow since the update.", "Negative", True),
        ),
    },
    570: {
        "name": "Dota 2",
        "short": "Dota 2",
        "hourly_reviews": 43,
        "latest_negative_share": 0.53,
        "terms": ("queue", "patch", "support", "ranked"),
        "examples": (
            (12, "Matchmaking keeps putting me in uneven games.", "Negative", False),
            (35, "The latest patch made playing support more fun.", "Positive", True),
        ),
    },
    4115450: {
        "name": "Phantom Blade Zero",
        "short": "Phantom Blade Zero",
        "hourly_reviews": 34,
        "latest_negative_share": 0.32,
        "terms": ("parry", "stutter", "boss", "combo"),
        "examples": (
            (17, "The combat feels responsive once the parry timing clicks.", "Positive", True),
            (42, "Performance drops during larger fights.", "Negative", False),
        ),
    },
}


def demo_snapshot(game_id):
    """Return hourly review and sentiment counts for one game."""
    game = GAMES[game_id]
    random = Random(game_id)
    volume_random = Random(game_id)
    buckets = []

    for offset in range(167, -1, -1):
        game_reviews = volume_random.randint(game["hourly_reviews"] - 15, game["hourly_reviews"] + 15)
        if offset == 0:
            game_reviews = 180 if game_id == 730 else game["hourly_reviews"] + 65
        elif game_id == 730 and offset <= 23:
            game_reviews = 47 + int(offset <= 19)

        # Leave a small share unclassified to distinguish collection from analysis.
        classified = game_reviews - round(game_reviews * 0.06)
        negative_share = game["latest_negative_share"] if offset == 0 else random.uniform(0.24, 0.43)
        negative = round(classified * negative_share)
        neutral = round(classified * random.uniform(0.12, 0.22))
        positive = classified - negative - neutral

        buckets.append({
            "end": SNAPSHOT_AT - timedelta(hours=offset),
            "reviews": game_reviews,
            "counts": (negative, neutral, positive),
        })

    # Synthetic game-wide frequencies for the latest hour and its baseline.
    rates = {
        730: ((24, 11), (14, 7), (12, 6), (10, 5)),
        570: ((21, 9), (17, 8), (13, 7), (9, 5)),
        4115450: ((28, 12), (15, 6), (14, 7), (12, 7)),
    }
    terms = [
        (keyword, recent, baseline, recent / baseline)
        for keyword, (recent, baseline) in zip(game["terms"], rates[game_id])
    ]
    return buckets, sorted(terms, key=lambda term: term[3], reverse=True)


def sentiment_alert(buckets):
    """Compare the last hour with the 24 hours immediately before it."""
    recent = buckets[-1]["counts"]
    baseline = tuple(map(sum, zip(*(bucket["counts"] for bucket in buckets[-25:-1]))))
    recent_count, baseline_count = sum(recent), sum(baseline)
    if min(recent_count, baseline_count) < 20:
        return None
    recent_rate = recent[0] / recent_count
    baseline_rate = baseline[0] / baseline_count
    change = recent_rate - baseline_rate
    if change < 0.15:
        return None
    return recent_rate, baseline_rate, change, recent_count, baseline_count
