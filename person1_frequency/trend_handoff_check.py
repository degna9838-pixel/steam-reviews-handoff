
"""End-to-end acceptance check for Person 1's trend data interface."""

from datetime import datetime, timedelta, timezone
from time import perf_counter

from trend_analysis.status import get_sync_status
from trend_analysis.storage import get_top_keywords


GAMES = {
    730: "CS2",
    570: "Dota 2",
    4115450: "Phantom Blade Zero",
}

CONSUMER_NAME = "trend_live"


def print_window(label, result):
    print(f"\n  {label}")
    print("  Stored reviews:", result["stored_reviews_in_window"])
    print("  Usable reviews:", result["total_reviews"])
    print("  Distinct keywords:", len(result["keywords"]))

    print("  Top 5 by DF:")

    for item in result["keywords"][:5]:
        print(
            f"    {item['term']:16s}"
            f"TF={item['tf']:5d} "
            f"DF={item['df']:5d} "
            f"Rate={item['document_rate']:.2%}"
        )


def main():
    # Freeze one common UTC time for all games and windows.
    end = datetime.now(timezone.utc)
    recent_start = end - timedelta(hours=1)
    baseline_start = recent_start - timedelta(hours=24)

    print("===== PERSON 1 HANDOFF ACCEPTANCE =====")
    print("Baseline:", baseline_start.isoformat(), "to", recent_start.isoformat())
    print("Recent:  ", recent_start.isoformat(), "to", end.isoformat())

    for app_id, name in GAMES.items():
        print(f"\n{'=' * 55}")
        print(f"GAME: {name} ({app_id})")

        status = get_sync_status(
            app_id=app_id,
            consumer_name=CONSUMER_NAME,
        )

        print("Stream matches:", status["stream_matches"])
        print("Consumer lag:", status["consumer_lag_events"])
        print("Backfill:", status["backfill_status"])
        print("Provisional ready:", status["ready_for_provisional_analysis"])

        if not status["stream_matches"]:
            print("SKIPPED: consumer and API stream identities differ.")
            continue

        start = perf_counter()

        baseline = get_top_keywords(
            app_id=app_id,
            start_time=baseline_start,
            end_time=recent_start,
            top_n=None,
            consumer_name=CONSUMER_NAME,
        )

        recent = get_top_keywords(
            app_id=app_id,
            start_time=recent_start,
            end_time=end,
            top_n=None,
            consumer_name=CONSUMER_NAME,
        )

        elapsed = perf_counter() - start

        # Basic interface contract checks.
        for result in (baseline, recent):
            assert result["app_id"] == app_id
            assert result["total_reviews"] <= result["stored_reviews_in_window"]
            assert all(
                0 <= item["df"] <= result["total_reviews"]
                for item in result["keywords"]
            )

        print_window("BASELINE (previous 24h)", baseline)
        print_window("RECENT (last 1h)", recent)

        if baseline["total_reviews"] == 0:
            print("\n  WARNING: Empty baseline; downstream trend scoring needs fallback.")

        if recent["total_reviews"] == 0:
            print("\n  WARNING: No usable reviews in recent window.")

        if not status["ready_for_provisional_analysis"]:
            print("\n  WARNING: Source is not fully caught up / ready at health check.")

        print(f"\n  Total query time: {elapsed:.4f} seconds")

    print("\n===== ACCEPTANCE CHECK FINISHED =====")


if __name__ == "__main__":
    main()
