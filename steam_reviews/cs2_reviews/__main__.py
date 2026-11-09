from __future__ import annotations

import argparse
import importlib.metadata
import json
import logging
from logging.handlers import RotatingFileHandler
import platform
import sys
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from .config import ROOT, GAMES, Settings, db_path, get_game
from .storage import Store


def configure_logging(sample: bool = False) -> None:
    logs = ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (ROOT / "work").mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / ("sample.log" if sample else "service.log"),
                                  maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.StreamHandler(), handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def load_sample(store: Store) -> dict:
    raw = json.loads((ROOT / "examples" / "synthetic_reviews.json").read_text(encoding="utf-8"))
    if store.app_id != 730:
        title = {570: "Dota 2", 4115450: "Phantom Blade Zero"}[store.app_id]
        raw = [{**row, "review": row["review"].replace("CS2", title).replace("cs2", title)} for row in raw]
    result = store.apply_page(raw)
    store.set("dataset_type", "synthetic_examples_not_real_steam_reviews")
    return result


def export_path(store: Store) -> Path:
    return store.path.with_name("sample_reviews_current.jsonl" if store.path.name == "sample.sqlite3" else "reviews_current.jsonl")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local multi-game English Steam review data service")
    parser.add_argument("--config", type=str, help="Optional config JSON path")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Start API and independent collectors for configured games")
    serve.add_argument("--sample", action="store_true", help="Offline synthetic databases; no Steam requests")
    collect = sub.add_parser("collect", help="One bounded collection cycle per selected game")
    collect.add_argument("--max-pages", type=int)
    backfill = sub.add_parser("backfill", help="Back up and expand each selected game's history scope")
    backfill.add_argument("--days", type=int, default=30)
    sample_parser = sub.add_parser("sample", help="Load separate synthetic examples")
    export = sub.add_parser("export", help="Export per-game UTF-8 JSONL and matching manifest")
    export.add_argument("--sample", action="store_true")
    reprocess = sub.add_parser("reprocess", help="Back up and re-clean saved source records; no Steam calls")
    reprocess.add_argument("--sample", action="store_true")
    for command in (serve, collect, backfill, sample_parser, export, reprocess):
        command.add_argument("--app-id", type=int, choices=list(GAMES), help="Select one game; omitted means all configured games")
    sub.add_parser("doctor", help="Show environment, games and paths without contacting Steam")
    args = parser.parse_args(argv)
    settings = Settings.load(Path(args.config).resolve() if args.config else None)
    sample = args.command == "sample" or getattr(args, "sample", False)
    configure_logging(sample)
    app_ids = (args.app_id,) if getattr(args, "app_id", None) is not None else settings.app_ids
    if args.command == "doctor":
        versions = {name: importlib.metadata.version(name) for name in ("fastapi", "uvicorn", "httpx", "langdetect", "simplemma")}
        print(json.dumps({"python": platform.python_version(), "platform": platform.platform(),
                          "project": str(ROOT), "live_database": str(db_path()), "dependencies": versions,
                          "settings": settings.__dict__, "games": [{**asdict(get_game(i)), "live_database": str(db_path(app_id=i)),
                          "sample_database": str(db_path(True, i))} for i in app_ids]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "collect" and args.max_pages is not None:
        if args.max_pages < 1:
            parser.error("--max-pages must be positive")
        settings = replace(settings, max_pages_per_cycle=args.max_pages)
    if args.command == "backfill" and args.days < 1:
        parser.error("--days must be positive")
    stores = {i: Store(db_path(sample, i), app_id=i) for i in app_ids}
    from .collector import Collector, CollectorLock
    from .maintenance import ensure_current_cleaning
    from .storage import utc_now
    if args.command == "serve":
        import uvicorn
        from .api import create_app
        for i, store in stores.items():
            upgrade = None
            if sample or store.needs_reprocess():
                with CollectorLock(store.path.with_suffix(".lock")):
                    if sample:
                        load_sample(store)
                    upgrade = ensure_current_cleaning(store)
            if upgrade:
                print(json.dumps({"app_id": i, "cleaning_upgrade": upgrade}, ensure_ascii=False), flush=True)
        print(f"API: http://{settings.host}:{settings.port}/docs", flush=True)
        print("Mode: OFFLINE SYNTHETIC SAMPLE" if sample else "Mode: LIVE; all selected games collect independently", flush=True)
        uvicorn.run(create_app(next(iter(stores.values())), settings, live=not sample, stores=stores),
                    host=settings.host, port=settings.port, workers=1)
        return 0
    from .coordinator import RequestGate
    gate = RequestGate(settings.request_delay_seconds)
    failed = False
    for i, store in stores.items():
        try:
            if args.command == "export":
                target = export_path(store)
                result = {"count": store.export(target), "output": str(target), "manifest": str(target.with_suffix(".manifest.json"))}
            else:
                with CollectorLock(store.path.with_suffix(".lock")):
                    if args.command == "sample":
                        result = load_sample(store)
                        ensure_current_cleaning(store)
                        store.export(export_path(store))
                    elif args.command == "reprocess":
                        result = ensure_current_cleaning(store, force=True)
                    elif args.command == "backfill":
                        stamp = utc_now().replace(":", "").replace("-", "")
                        target = store.path.parent / "backups" / f"reviews_before_backfill_{stamp}_{uuid.uuid4().hex[:8]}.sqlite3"
                        store.backup(target)
                        result = {**store.expand_scope(args.days), "backup": str(target)}
                    else:
                        ensure_current_cleaning(store)
                        collector = Collector(store, settings, request_gate=gate)
                        try:
                            result = collector.collect_once()
                        finally:
                            collector.close()
            print(json.dumps({"app_id": i, **result}, ensure_ascii=False, indent=2), flush=True)
        except Exception as exc:
            failed = True
            print(json.dumps({"app_id": i, "error": str(exc)}, ensure_ascii=False), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        logging.getLogger(__name__).error("%s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
