from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .client import (
    doctor,
    export_dashboard,
    export_llm_jobs,
    export_reviews,
    export_samples,
    import_llm_labels,
    rebuild,
    run_all_games,
    run_game,
    summarize,
    verify,
)
from .evaluate import evaluate_file
from .settings import (
    DEFAULT_ALPHA,
    DEFAULT_BASELINE_HOURS,
    DEFAULT_CHANGE_THRESHOLD,
    DEFAULT_LIMIT,
    DEFAULT_MIN_MENTIONS,
    DEFAULT_POLL_SECONDS,
    DEFAULT_WINDOW_HOURS,
    LIVE_CONSUMER,
    validate_app_id,
)
from .util import parse_utc


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative finite number")
    return parsed


def _alpha(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or not 0 < parsed < 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def _now(value: str):
    try:
        return parse_utc(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid UTC timestamp: {exc}") from exc


def _add_common_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--consumer", default=LIVE_CONSUMER)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-batches", type=_positive_int)
    parser.add_argument("--poll-seconds", type=_positive_float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--limit", type=_positive_int, default=DEFAULT_LIMIT)
    parser.add_argument("--no-summary", action="store_true")
    parser.add_argument("--now", type=_now)
    parser.add_argument("--window-hours", type=_positive_float, default=DEFAULT_WINDOW_HOURS)
    parser.add_argument("--baseline-hours", type=_positive_float, default=DEFAULT_BASELINE_HOURS)
    parser.add_argument("--min-mentions", type=_positive_int, default=DEFAULT_MIN_MENTIONS)
    parser.add_argument("--change-threshold", type=_non_negative_float, default=DEFAULT_CHANGE_THRESHOLD)
    parser.add_argument("--alpha", type=_alpha, default=DEFAULT_ALPHA)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m sentiment_analysis")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Consume /v1/reviews/changes and analyze sentiment")
    run.add_argument("--app-id", type=int)
    run.add_argument("--all-games", action="store_true")
    _add_common_run(run)

    doc = sub.add_parser("doctor", help="Check the local API, games and backfill state")
    doc.add_argument("--base-url", default="http://127.0.0.1:8000")

    summary = sub.add_parser("summarize", help="Build a window summary for one game")
    summary.add_argument("--app-id", type=int, required=True)
    summary.add_argument("--base-url", default="http://127.0.0.1:8000")
    summary.add_argument("--consumer", default=LIVE_CONSUMER)
    summary.add_argument("--no-fetch", action="store_true")
    summary.add_argument("--now", type=_now)
    summary.add_argument("--window-hours", type=_positive_float, default=DEFAULT_WINDOW_HOURS)
    summary.add_argument("--baseline-hours", type=_positive_float, default=DEFAULT_BASELINE_HOURS)
    summary.add_argument("--min-mentions", type=_positive_int, default=DEFAULT_MIN_MENTIONS)
    summary.add_argument("--change-threshold", type=_non_negative_float, default=DEFAULT_CHANGE_THRESHOLD)
    summary.add_argument("--alpha", type=_alpha, default=DEFAULT_ALPHA)
    summary.add_argument("--out", type=Path)

    rebuild_parser = sub.add_parser("rebuild", help="Rebuild analysis from the local review mirror")
    rebuild_parser.add_argument("--app-id", type=int, required=True)
    rebuild_parser.add_argument("--consumer", default=LIVE_CONSUMER)

    samples = sub.add_parser("export-samples", help="Export a stratified sample for LLM calibration")
    samples.add_argument("--app-id", type=int, required=True)
    samples.add_argument("--consumer", default=LIVE_CONSUMER)
    samples.add_argument("--limit", type=_positive_int, default=120)
    samples.add_argument("--out", type=Path, required=True)

    dashboard = sub.add_parser("export-dashboard", help="Export compact sentiment time series")
    dashboard.add_argument("--app-id", type=int, required=True)
    dashboard.add_argument("--consumer", default=LIVE_CONSUMER)
    dashboard.add_argument("--days", type=_positive_int, default=7)
    dashboard.add_argument("--bucket-hours", type=_positive_int, default=1)
    dashboard.add_argument("--out", type=Path, required=True)

    reviews = sub.add_parser("export-reviews", help="Export per-review entity sentiment JSONL")
    reviews.add_argument("--app-id", type=int, required=True)
    reviews.add_argument("--consumer", default=LIVE_CONSUMER)
    reviews.add_argument("--out", type=Path, required=True)

    verify_parser = sub.add_parser("verify", help="Check analysis database integrity")
    verify_parser.add_argument("--app-id", type=int, required=True)
    verify_parser.add_argument("--consumer", default=LIVE_CONSUMER)

    evaluate_parser = sub.add_parser("evaluate", help="Evaluate labels against a human gold set")
    evaluate_parser.add_argument("--input", type=Path, required=True)
    evaluate_parser.add_argument("--gold-field", default="human_label")
    evaluate_parser.add_argument("--pred-field", default="vader_label")
    evaluate_parser.add_argument("--entity-field", default="entity_id")
    evaluate_parser.add_argument("--out", type=Path, required=True)

    jobs = sub.add_parser("export-llm-jobs", help="Export review jobs for LLM labeling")
    jobs.add_argument("--app-id", type=int, required=True)
    jobs.add_argument("--consumer", default=LIVE_CONSUMER)
    jobs.add_argument("--out", type=Path, required=True)
    jobs.add_argument("--limit", type=_positive_int)
    jobs.add_argument("--include-labeled", action="store_true")
    jobs.add_argument("--model", default=None)

    labels = sub.add_parser("import-llm-labels", help="Import LLM labeling predictions")
    labels.add_argument("--app-id", type=int, required=True)
    labels.add_argument("--consumer", default=LIVE_CONSUMER)
    labels.add_argument("--predictions", type=Path, required=True)
    labels.add_argument("--model", default=None)
    labels.add_argument("--prompt-version", default=None)

    serve = sub.add_parser("serve", help="Serve read-only sentiment results to the dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8100)
    serve.add_argument("--consumer", default=LIVE_CONSUMER)
    serve.add_argument("--collector-url", default="http://127.0.0.1:8000")

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return doctor(args.base_url)
        if args.command == "run":
            if args.all_games:
                if args.app_id is not None:
                    parser.error("--all-games and --app-id are mutually exclusive")
                if not args.once:
                    parser.error("--all-games requires --once; run one process per game for continuous mode")
                return run_all_games(args)
            if args.app_id is None:
                parser.error("--app-id or --all-games is required")
            validate_app_id(args.app_id)
            run_game(
                args.app_id,
                base_url=args.base_url,
                consumer=args.consumer,
                once=args.once,
                max_batches=args.max_batches,
                poll_seconds=args.poll_seconds,
                limit=args.limit,
                write_summary_file=not args.no_summary,
                now=args.now,
                window_hours=args.window_hours,
                baseline_hours=args.baseline_hours,
                min_mentions=args.min_mentions,
                change_threshold=args.change_threshold,
                alpha=args.alpha,
            )
            return 0
        if args.command == "summarize":
            validate_app_id(args.app_id)
            summarize(
                args.app_id,
                base_url=args.base_url,
                consumer=args.consumer,
                no_fetch=args.no_fetch,
                now=args.now,
                window_hours=args.window_hours,
                baseline_hours=args.baseline_hours,
                min_mentions=args.min_mentions,
                change_threshold=args.change_threshold,
                alpha=args.alpha,
                out=args.out,
            )
            return 0
        if args.command == "rebuild":
            validate_app_id(args.app_id)
            rebuild(args.app_id, args.consumer)
            return 0
        if args.command == "export-samples":
            validate_app_id(args.app_id)
            export_samples(args.app_id, args.consumer, args.limit, args.out)
            return 0
        if args.command == "export-dashboard":
            validate_app_id(args.app_id)
            export_dashboard(args.app_id, args.consumer, args.days, args.bucket_hours, args.out)
            return 0
        if args.command == "export-reviews":
            validate_app_id(args.app_id)
            export_reviews(args.app_id, args.consumer, args.out)
            return 0
        if args.command == "verify":
            validate_app_id(args.app_id)
            return verify(args.app_id, args.consumer)
        if args.command == "evaluate":
            report = evaluate_file(
                args.input,
                gold_field=args.gold_field,
                pred_field=args.pred_field,
                entity_field=args.entity_field,
                out=args.out,
            )
            print(json.dumps({
                "n": report["overall"]["n"],
                "accuracy": report["overall"]["accuracy"],
                "macro_f1": report["overall"]["macro_f1"],
                "kappa": report["overall"]["kappa"],
                "errors": report["errors"],
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "export-llm-jobs":
            validate_app_id(args.app_id)
            kwargs = {"limit": args.limit, "include_labeled": args.include_labeled}
            if args.model:
                kwargs["model"] = args.model
            export_llm_jobs(args.app_id, args.consumer, args.out, **kwargs)
            return 0
        if args.command == "import-llm-labels":
            validate_app_id(args.app_id)
            kwargs = {}
            if args.model:
                kwargs["model"] = args.model
            if args.prompt_version:
                kwargs["prompt_version"] = args.prompt_version
            import_llm_labels(args.app_id, args.consumer, args.predictions, **kwargs)
            return 0
        if args.command == "serve":
            if not 1 <= args.port <= 65535:
                parser.error("--port must be between 1 and 65535")
            import uvicorn
            from .api import create_app
            uvicorn.run(create_app(args.consumer, args.collector_url), host=args.host, port=args.port)
            return 0
        parser.error("Unknown command")
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(json.dumps({"event": "error", "message": str(exc)}, ensure_ascii=False), flush=True)
        return 1
    return 0
