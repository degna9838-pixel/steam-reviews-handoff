"""Run trend detection for the three games and write the dashboard files.

Examples, from the steam_reviews directory:

    python -m trend_analysis.run --experiment
    python -m trend_analysis.run --once --skip-sync
    python -m trend_analysis.run --once
    python -m trend_analysis.run --loop --interval-seconds 60
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .cloud import resolve_base_url
from .detector import detect_trends, empty_result
from .experiment import run_fixture
from .export import analysis_root, export_results


GAMES = (730, 570, 4115450)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _unreachable_status(message):
    return {
        "mode": "unknown",
        "ready_for_provisional_analysis": False,
        "sync_status": "upstream_unreachable",
        "local_stream_id": None,
        "local_cursor": None,
        "historical_completeness_verified": False,
        "message": message,
    }


def analyze_game(app_id, *, consumer_name, base_url, end, skip_sync, data_root=None):
    """Sync one game when requested, then score whatever snapshot is already stored."""
    from .consumer import sync_one
    from .status import get_sync_status

    sync_error = None
    if not skip_sync:
        try:
            sync_one(app_id, consumer_name, base_url=base_url)
        except Exception as exc:
            sync_error = str(exc)

    status = None
    try:
        status = get_sync_status(
            app_id,
            consumer_name=consumer_name,
            base_url=base_url,
            data_root=data_root,
        )
    except Exception as exc:
        status = _unreachable_status(str(exc))

    try:
        result = detect_trends(
            app_id,
            consumer_name=consumer_name,
            end=end,
            data_root=data_root,
            sync_status=status,
        )
    except FileNotFoundError as exc:
        result = empty_result(
            app_id,
            consumer_name=consumer_name,
            data_status="local_database_missing",
            message=str(exc),
            end=end,
        )
    if sync_error:
        result["sync_error"] = sync_error
    if status and status.get("message"):
        result["status_message"] = status["message"]
    return result


def analyze_all(*, consumer_name, base_url, end, skip_sync, app_id=None):
    selected = (app_id,) if app_id is not None else GAMES
    return [
        analyze_game(
            game_id,
            consumer_name=consumer_name,
            base_url=base_url,
            end=end,
            skip_sync=skip_sync,
        )
        for game_id in selected
    ]


def print_summary(results):
    for result in results:
        print(
            f"app_id={result['app_id']} status={result['data_status']} "
            f"publishable={result['conclusions_publishable']} "
            f"recent_reviews={result['recent_reviews']} "
            f"baseline_reviews={result['baseline_reviews']} "
            f"trending={result['trending_terms'][:10]}"
        )


def run_experiment():
    destination = analysis_root() / "experiments"
    destination.mkdir(parents=True, exist_ok=True)
    report = run_fixture(destination / "fixture_data")
    output = destination / "fixture_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    export_results([report["default"]], root=destination / "dashboard_preview")
    print(f"Fixture report: {output}")
    print_summary([report["default"]])
    print(
        "Parameter check: threshold 10 trending=",
        report["strength_threshold_10"]["trending_terms"],
        "; min_recent_df 1 trending=",
        report["min_recent_df_1"]["trending_terms"],
    )
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Detect trending review terms for three Steam games")
    parser.add_argument("--experiment", action="store_true", help="Score the constructed fixture and exit")
    parser.add_argument("--once", action="store_true", help="Score the current local snapshots once")
    parser.add_argument("--loop", action="store_true", help="Sync and score until interrupted")
    parser.add_argument("--interval-seconds", type=float, default=60)
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--consumer", default="trend_cloud")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Collection API. Defaults to the shared cloud server.",
    )
    parser.add_argument("--end", help="UTC window end, for a reproducible run. Live runs should omit this.")
    parser.add_argument("--app-id", type=int, choices=GAMES)
    parser.add_argument("--output-root", help="Directory for JSON results. Default is analysis/.")
    args = parser.parse_args(argv)

    if args.experiment:
        run_experiment()
        return 0
    if args.loop and args.once:
        parser.error("Choose either --once or --loop")
    if args.interval_seconds <= 0:
        parser.error("--interval-seconds must be positive")
    if not args.loop:
        args.once = True

    while True:
        started = datetime.now(timezone.utc).isoformat()
        print(f"\nTrend run at {started}")
        results = analyze_all(
            consumer_name=args.consumer,
            base_url=resolve_base_url(args.base_url),
            end=args.end,
            skip_sync=args.skip_sync,
            app_id=args.app_id,
        )
        written = export_results(results, root=args.output_root)
        print_summary(results)
        print(f"Wrote {written['combined']}")
        if not args.loop:
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
