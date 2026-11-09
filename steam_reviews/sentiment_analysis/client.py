from __future__ import annotations

import json
import random
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from .aggregate import export_dashboard as build_dashboard
from .aggregate import summarize_game, write_summary
from .entities import EntityConfig, load_entity_config
from .llm import export_jobs as export_llm_jobs_file
from .llm import import_predictions as import_llm_predictions
from .sentiment import SentimentAnalyzer
from .settings import (
    ALGORITHM_VERSION,
    DEFAULT_ALPHA,
    DEFAULT_BASELINE_HOURS,
    DEFAULT_CHANGE_THRESHOLD,
    DEFAULT_LIMIT,
    DEFAULT_MIN_MENTIONS,
    DEFAULT_POLL_SECONDS,
    DEFAULT_WINDOW_HOURS,
    GAME_NAMES,
    LLM_MODEL_DEFAULT,
    LLM_PROMPT_VERSION,
    SAMPLE_SCHEMA_VERSION,
    SUPPORTED_APP_IDS,
    analysis_db_path,
    entity_config_path,
    summary_dir,
    validate_app_id,
)
from .store import (
    CONSUMER,
    analysis_rows,
    bind_state,
    connect,
    current_records,
    ensure_algorithm_binding,
    get_health,
    init_schema,
    rebuild as rebuild_analysis,
    record_algorithm_binding,
    record_health,
    save_batch,
    state_get,
    state_set,
)
from .util import atomic_write_json, iso_utc, parse_utc, utc_now


class ApiError(RuntimeError):
    pass


class ApiClient:
    def __init__(self, base_url: str, app_id: int | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.app_id = app_id
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None) -> dict:
        query = ("?" + urlencode(params)) if params else ""
        url = self.base_url + path + query
        try:
            with urlopen(url, timeout=self.timeout) as response:
                payload = json.load(response)
        except HTTPError as exc:
            raise ApiError(f"HTTP {exc.code} for {path}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ApiError(f"Network error for {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ApiError(f"Invalid JSON object from {path}")
        return payload

    def games(self) -> dict:
        return self._get("/v1/games")

    def health(self) -> dict:
        if self.app_id is None:
            raise ApiError("app_id is required")
        payload = self._get("/v1/health", {"app_id": self.app_id})
        if payload.get("app_id") != self.app_id:
            raise ApiError("Health app_id mismatch")
        return payload

    def changes(self, cursor: int, limit: int = DEFAULT_LIMIT) -> dict:
        if self.app_id is None:
            raise ApiError("app_id is required")
        return self._get("/v1/reviews/changes", {
            "app_id": self.app_id,
            "cursor": cursor,
            "limit": limit,
        })


def find_game(games: dict, app_id: int) -> dict:
    items = games.get("games")
    if not isinstance(items, list):
        raise ApiError("Invalid /v1/games response")
    for item in items:
        if item.get("app_id") == app_id:
            return item
    raise ApiError(f"app_id {app_id} is not in /v1/games")


def service_mode(games: dict, health: dict) -> str:
    if health.get("dataset_type") == "synthetic_examples_not_real_steam_reviews":
        return "sample"
    mode = games.get("mode") or health.get("mode")
    if mode == "offline":
        return "sample"
    if mode == "live":
        return "live"
    raise ApiError("Unknown service mode")


def validate_mode(consumer: str, mode: str) -> None:
    if mode == "sample" and "demo" not in consumer:
        raise ApiError("Sample service requires a consumer name containing 'demo'")
    if mode == "live" and "demo" in consumer:
        raise ApiError("Live service cannot use a demo consumer name")


def _default_now(db, mode: str):
    if mode == "sample":
        row = db.execute("SELECT MAX(created_at) FROM reviews").fetchone()
        if row and row[0]:
            from datetime import timedelta
            return parse_utc(row[0]) + timedelta(hours=1)
    return utc_now()


def _refresh_health(client: ApiClient, db, mode: str) -> dict:
    health = client.health()
    record_health(db, health, mode)
    return health


def _write_summary_for(
    db,
    config: EntityConfig,
    health: dict,
    *,
    mode: str,
    consumer: str,
    now=None,
    window_hours: float,
    baseline_hours: float,
    min_mentions: int,
    change_threshold: float,
    alpha: float,
    out_dir: Path | None = None,
) -> dict:
    summary = summarize_game(
        db,
        config,
        health,
        now=now or _default_now(db, mode),
        window_hours=window_hours,
        baseline_hours=baseline_hours,
        min_mentions=min_mentions,
        change_threshold=change_threshold,
        alpha=alpha,
    )
    tag = f"{window_hours:g}h_{baseline_hours:g}h"
    latest, history = write_summary(summary, out_dir or (summary_dir(config.app_id, consumer) / tag))
    summary["_latest_path"] = str(latest)
    summary["_history_path"] = str(history)
    return summary


def run_game(
    app_id: int,
    *,
    base_url: str,
    consumer: str,
    once: bool,
    max_batches: int | None,
    poll_seconds: float,
    limit: int,
    write_summary_file: bool,
    now=None,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    baseline_hours: float = DEFAULT_BASELINE_HOURS,
    min_mentions: int = DEFAULT_MIN_MENTIONS,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    alpha: float = DEFAULT_ALPHA,
) -> dict:
    validate_app_id(app_id)
    client = ApiClient(base_url, app_id)
    config = load_entity_config(app_id)
    analyzer = SentimentAnalyzer()
    db = connect(analysis_db_path(app_id, consumer))
    init_schema(db)
    games = client.games()
    health = client.health()
    mode = service_mode(games, health)
    validate_mode(consumer, mode)
    game = find_game(games, app_id)
    if game.get("enabled") is False:
        raise ApiError(f"app_id {app_id} is not enabled by this service")
    stream_id = health.get("stream_id") or game.get("stream_id")
    if not isinstance(stream_id, str) or not stream_id:
        raise ApiError("Service did not provide stream_id")
    if game.get("stream_id") and game["stream_id"] != stream_id:
        raise ApiError("Game/health stream_id mismatch")
    bind_state(db, app_id, stream_id, mode)
    state_set(db, "consumer", consumer)
    ensure_algorithm_binding(db, config, ALGORITHM_VERSION, analyzer.lexicon_hash)
    record_algorithm_binding(db, config, ALGORITHM_VERSION, analyzer.lexicon_hash)
    db.commit()
    record_health(db, health, mode)
    cursor = int(state_get(db, "cursor", "0") or "0")
    batches = 0
    total_events = 0
    print(json.dumps({
        "event": "start", "app_id": app_id, "game": GAME_NAMES.get(app_id, game.get("name")),
        "mode": mode, "consumer": consumer, "cursor": cursor, "stream_id": stream_id,
        "db": str(analysis_db_path(app_id, consumer)),
    }, ensure_ascii=False), flush=True)
    while True:
        batch = client.changes(cursor, limit=limit)
        if batch.get("schema_version") != "1.0":
            raise ApiError("Unsupported changes schema_version")
        if batch.get("stream_id") != stream_id:
            raise ApiError("Changes stream_id mismatch")
        cursor, changed = save_batch(
            db, batch, cursor,
            app_id=app_id,
            config=config,
            analyzer=analyzer,
            algorithm_version=ALGORITHM_VERSION,
        )
        batches += 1
        total_events += len(batch.get("items", []))
        print(json.dumps({
            "event": "batch", "app_id": app_id, "events": len(batch.get("items", [])),
            "updated": changed, "cursor": cursor, "has_more": bool(batch.get("has_more")),
        }, ensure_ascii=False), flush=True)
        if max_batches is not None and batches >= max_batches:
            print(json.dumps({"event": "max_batches", "app_id": app_id, "batches": batches}, ensure_ascii=False), flush=True)
            break
        if batch.get("has_more"):
            continue
        if once:
            break
        time.sleep(poll_seconds)
    try:
        health = _refresh_health(client, db, mode)
    except ApiError as exc:
        print(json.dumps({"event": "health_warning", "app_id": app_id, "message": str(exc)}, ensure_ascii=False), flush=True)
        health, _ = get_health(db)
    summary = None
    if write_summary_file:
        summary = _write_summary_for(
            db, config, health, mode=mode, consumer=consumer, now=now,
            window_hours=window_hours, baseline_hours=baseline_hours,
            min_mentions=min_mentions, change_threshold=change_threshold, alpha=alpha,
        )
        print(json.dumps({
            "event": "summary", "app_id": app_id, "path": summary["_latest_path"],
            "data_status": summary["data_status"], "entities": len(summary["entities"]),
        }, ensure_ascii=False), flush=True)
    db.close()
    return {
        "app_id": app_id,
        "mode": mode,
        "batches": batches,
        "events": total_events,
        "cursor": cursor,
        "summary": summary,
    }


def run_all_games(args) -> int:
    failures = 0
    for app_id in SUPPORTED_APP_IDS:
        try:
            run_game(
                app_id,
                base_url=args.base_url,
                consumer=args.consumer,
                once=True,
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
        except Exception as exc:
            failures += 1
            print(json.dumps({"event": "game_error", "app_id": app_id, "message": str(exc)}, ensure_ascii=False), flush=True)
    return 1 if failures else 0


def doctor(base_url: str) -> int:
    client = ApiClient(base_url)
    games = client.games()
    rows = []
    for app_id in SUPPORTED_APP_IDS:
        try:
            item = find_game(games, app_id)
            client.app_id = app_id
            health = client.health()
            rows.append({
                "app_id": app_id,
                "name": item.get("name"),
                "enabled": item.get("enabled"),
                "mode": games.get("mode"),
                "dataset_type": health.get("dataset_type"),
                "review_count": health.get("review_count"),
                "data_ready": health.get("data_ready"),
                "backfill_status": health.get("backfill_status"),
                "collector_state": health.get("collector_state"),
                "stream_id": health.get("stream_id"),
                "pipeline_versions": health.get("pipeline_versions"),
                "last_success_at": health.get("last_success_at"),
                "last_error": health.get("last_error"),
            })
        except Exception as exc:
            rows.append({"app_id": app_id, "error": str(exc)})
    print(json.dumps({"base_url": base_url, "mode": games.get("mode"), "games": rows}, ensure_ascii=False, indent=2))
    return 0


def summarize(
    app_id: int,
    *,
    base_url: str,
    consumer: str,
    no_fetch: bool,
    now=None,
    window_hours: float,
    baseline_hours: float,
    min_mentions: int,
    change_threshold: float,
    alpha: float,
    out: Path | None = None,
) -> dict:
    validate_app_id(app_id)
    path = analysis_db_path(app_id, consumer)
    if not path.exists():
        raise ApiError(f"Analysis database not found: {path}")
    db = connect(path)
    config = load_entity_config(app_id)
    mode = state_get(db, "mode", "live") or "live"
    state_set(db, "consumer", consumer)
    db.commit()
    health, _ = get_health(db)
    if not no_fetch:
        try:
            client = ApiClient(base_url, app_id)
            health = _refresh_health(client, db, mode)
        except ApiError as exc:
            print(json.dumps({"event": "health_warning", "message": str(exc)}, ensure_ascii=False), flush=True)
    summary = _write_summary_for(
        db, config, health, mode=mode, consumer=consumer, now=now,
        window_hours=window_hours, baseline_hours=baseline_hours,
        min_mentions=min_mentions, change_threshold=change_threshold, alpha=alpha,
        out_dir=Path(out).parent if out else None,
    )
    if out:
        atomic_write_json(Path(out), summary)
    db.close()
    print(json.dumps({
        "app_id": app_id, "latest": summary["_latest_path"], "data_status": summary["data_status"],
        "entities": [
            {"entity_id": item["entity_id"], "current": item["current"], "baseline": item["baseline"],
             "change": item["change"], "change_flag": item["change_flag"], "alert_type": item["alert_type"]}
            for item in summary["entities"]
        ],
    }, ensure_ascii=False, indent=2))
    return summary


def rebuild(app_id: int, consumer: str) -> dict:
    validate_app_id(app_id)
    path = analysis_db_path(app_id, consumer)
    if not path.exists():
        raise ApiError(f"Analysis database not found: {path}")
    db = connect(path)
    config = load_entity_config(app_id)
    analyzer = SentimentAnalyzer()
    state_set(db, "consumer", consumer)
    db.commit()
    count = rebuild_analysis(
        db, app_id=app_id, config=config, analyzer=analyzer, algorithm_version=ALGORITHM_VERSION
    )
    health, _ = get_health(db)
    mode = state_get(db, "mode", "live") or "live"
    summary = _write_summary_for(
        db, config, health, mode=mode, consumer=consumer, now=_default_now(db, mode),
        window_hours=DEFAULT_WINDOW_HOURS, baseline_hours=DEFAULT_BASELINE_HOURS,
        min_mentions=DEFAULT_MIN_MENTIONS, change_threshold=DEFAULT_CHANGE_THRESHOLD, alpha=DEFAULT_ALPHA,
    )
    db.close()
    print(json.dumps({"event": "rebuild", "app_id": app_id, "reviews": count, "summary": summary["_latest_path"]}, ensure_ascii=False))
    return summary


def export_samples(app_id: int, consumer: str, limit: int, out: Path) -> int:
    validate_app_id(app_id)
    if limit <= 0:
        raise ValueError("limit must be positive")
    path = analysis_db_path(app_id, consumer)
    db = connect(path)
    config = load_entity_config(app_id)
    rows = db.execute(
        """
        SELECT a.review_id,a.revision,a.entity_id,a.sentiment_label,a.sentiment_score,
               a.evidence,a.created_at,r.record_json
        FROM analysis a
        JOIN review_status s ON s.review_id=a.review_id AND s.revision=a.revision
        JOIN reviews r ON r.review_id=a.review_id
        WHERE a.algorithm_version=? AND s.status='analyzed'
        ORDER BY a.entity_id,a.sentiment_label,a.created_at
        """,
        (ALGORITHM_VERSION,),
    ).fetchall()
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        record = json.loads(row["record_json"])
        text = record.get("text_clean") or ""
        if not text.strip():
            continue
        key = (row["entity_id"], row["sentiment_label"])
        groups.setdefault(key, []).append({
            "sample_id": f"{app_id}:{row['review_id']}:{row['revision']}:{row['entity_id']}",
            "app_id": app_id,
            "game": config.game,
            "review_id": row["review_id"],
            "revision": row["revision"],
            "entity_id": row["entity_id"],
            "entity_name": config.by_id(row["entity_id"]).name,
            "vader_label": row["sentiment_label"],
            "vader_score": row["sentiment_score"],
            "vader_evidence": row["evidence"],
            "created_at": row["created_at"],
            "text_clean": text,
        })
    order = sorted(groups)
    for items in groups.values():
        random.Random(42).shuffle(items)
    selected: list[dict] = []
    positions = {key: 0 for key in order}
    while len(selected) < limit:
        progressed = False
        for key in order:
            position = positions[key]
            if position < len(groups[key]):
                selected.append(groups[key][position])
                positions[key] = position + 1
                progressed = True
                if len(selected) >= limit:
                    break
        if not progressed:
            break
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as stream:
        for item in selected:
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=False) + "\n")
    atomic_write_json(out.with_suffix(out.suffix + ".manifest.json"), {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "app_id": app_id,
        "created_at": iso_utc(utc_now()),
        "limit": limit,
        "count": len(selected),
        "algorithm_version": ALGORITHM_VERSION,
        "entity_config_version": config.version,
        "source": "VADER stratified sample; not human ground truth",
    })
    db.close()
    print(json.dumps({"event": "export_samples", "app_id": app_id, "count": len(selected), "path": str(out)}, ensure_ascii=False))
    return len(selected)


def export_reviews(app_id: int, consumer: str, out: Path) -> int:
    validate_app_id(app_id)
    path = analysis_db_path(app_id, consumer)
    if not path.exists():
        raise ApiError(f"Analysis database not found: {path}")
    db = connect(path)
    rows = db.execute(
        """
        SELECT a.review_id,a.revision,a.entity_id,a.sentiment_label,a.sentiment_score,
               a.evidence,a.created_at,a.pipeline_version,a.algorithm_version,
               a.entity_config_version,a.analyzed_at,a.reused_from_revision,a.scorer
        FROM effective_analysis a
        JOIN review_status s ON s.review_id=a.review_id AND s.revision=a.revision
        WHERE s.status='analyzed'
        ORDER BY a.created_at,a.review_id,a.entity_id
        """
    ).fetchall()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            payload = {"app_id": app_id, **{key: row[key] for key in row.keys()}}
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    db.close()
    print(json.dumps({"event": "export_reviews", "app_id": app_id, "rows": len(rows), "path": str(out)}, ensure_ascii=False))
    return len(rows)


def verify(app_id: int, consumer: str) -> int:
    validate_app_id(app_id)
    path = analysis_db_path(app_id, consumer)
    if not path.exists():
        raise ApiError(f"Analysis database not found: {path}")
    db = connect(path)
    issues = []
    if state_get(db, "app_id") != str(app_id):
        issues.append("state app_id mismatch")
    if not state_get(db, "stream_id"):
        issues.append("state stream_id missing")
    if state_get(db, "cursor") is None:
        issues.append("state cursor missing")
    reviews = db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    statuses = db.execute("SELECT COUNT(*) FROM review_status").fetchone()[0]
    if reviews != statuses:
        issues.append(f"reviews={reviews} but review_status={statuses}")
    orphan = db.execute(
        "SELECT COUNT(*) FROM analysis a LEFT JOIN review_status s ON s.review_id=a.review_id WHERE s.review_id IS NULL"
    ).fetchone()[0]
    if orphan:
        issues.append(f"{orphan} analysis rows without review_status")
    bad_status = db.execute(
        """
        SELECT COUNT(*) FROM analysis a
        JOIN review_status s ON s.review_id=a.review_id
        WHERE s.status!='analyzed' OR a.revision!=s.revision
        """
    ).fetchone()[0]
    if bad_status:
        issues.append(f"{bad_status} analysis rows for excluded/skipped/stale revisions")
    missing_status = db.execute(
        "SELECT COUNT(*) FROM review_status s LEFT JOIN reviews r ON r.review_id=s.review_id WHERE r.review_id IS NULL"
    ).fetchone()[0]
    if missing_status:
        issues.append(f"{missing_status} review_status rows without reviews")
    algorithms = [row[0] for row in db.execute("SELECT DISTINCT algorithm_version FROM analysis").fetchall()]
    stored_algorithm = state_get(db, "algorithm_version")
    unexpected = [value for value in algorithms if value != stored_algorithm]
    if unexpected:
        issues.append("analysis contains other algorithm versions: " + ", ".join(map(str, unexpected)))
    llm_rows = db.execute("SELECT COUNT(*) FROM llm_analysis").fetchone()[0]
    llm_bad = db.execute(
        """
        SELECT COUNT(*) FROM llm_analysis l
        JOIN review_status s ON s.review_id=l.review_id AND s.revision=l.revision
        WHERE s.status='analyzed'
          AND ((l.mentioned=1 AND l.sentiment_label NOT IN ('positive','negative','neutral'))
            OR (l.mentioned=0 AND l.sentiment_label IS NOT NULL))
        """
    ).fetchone()[0]
    if llm_bad:
        issues.append(f"{llm_bad} invalid current LLM analysis rows")
    llm_stale = db.execute(
        """
        SELECT COUNT(*) FROM llm_analysis l
        LEFT JOIN review_status s ON s.review_id=l.review_id AND s.revision=l.revision
        WHERE s.review_id IS NULL
        """
    ).fetchone()[0]
    analyzed = db.execute("SELECT COUNT(*) FROM review_status WHERE status='analyzed'").fetchone()[0]
    llm_labeled = db.execute(
        "SELECT COUNT(DISTINCT l.review_id) FROM llm_analysis l JOIN review_status s ON s.review_id=l.review_id AND s.revision=l.revision WHERE s.status='analyzed'"
    ).fetchone()[0]
    result = {
        "event": "verify",
        "app_id": app_id,
        "consumer": consumer,
        "reviews": reviews,
        "review_status": statuses,
        "analysis_rows": db.execute("SELECT COUNT(*) FROM analysis").fetchone()[0],
        "llm_analysis_rows": llm_rows,
        "llm_stale_rows": llm_stale,
        "llm_labeled_reviews": llm_labeled,
        "llm_coverage": (llm_labeled / analyzed) if analyzed else None,
        "algorithm_versions": algorithms,
        "llm_model": state_get(db, "llm_model"),
        "llm_prompt_version": state_get(db, "llm_prompt_version"),
        "cursor": state_get(db, "cursor"),
        "stream_id": state_get(db, "stream_id"),
        "issues": issues,
        "ok": not issues,
    }
    db.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


def export_llm_jobs(
    app_id: int,
    consumer: str,
    out: Path,
    *,
    limit: int | None = None,
    include_labeled: bool = False,
    model: str = LLM_MODEL_DEFAULT,
) -> int:
    validate_app_id(app_id)
    path = analysis_db_path(app_id, consumer)
    if not path.exists():
        raise ApiError(f"Analysis database not found: {path}")
    db = connect(path)
    init_schema(db)
    config = load_entity_config(app_id)
    count = export_llm_jobs_file(
        db, config, out, app_id=app_id, model=model,
        include_labeled=include_labeled, limit=limit,
    )
    db.close()
    print(json.dumps({"event": "export_llm_jobs", "app_id": app_id, "jobs": count, "path": str(out)}, ensure_ascii=False))
    return count


def import_llm_labels(
    app_id: int,
    consumer: str,
    predictions: Path,
    *,
    model: str = LLM_MODEL_DEFAULT,
    prompt_version: str = LLM_PROMPT_VERSION,
) -> dict:
    validate_app_id(app_id)
    path = analysis_db_path(app_id, consumer)
    if not path.exists():
        raise ApiError(f"Analysis database not found: {path}")
    db = connect(path)
    init_schema(db)
    config = load_entity_config(app_id)
    result = import_llm_predictions(
        db, config, predictions, app_id=app_id, model=model, prompt_version=prompt_version,
    )
    db.close()
    print(json.dumps({"event": "import_llm_labels", "app_id": app_id, **result}, ensure_ascii=False))
    return result


def export_dashboard(app_id: int, consumer: str, days: int, bucket_hours: int, out: Path) -> dict:
    validate_app_id(app_id)
    path = analysis_db_path(app_id, consumer)
    if not path.exists():
        raise ApiError(f"Analysis database not found: {path}")
    db = connect(path)
    config = load_entity_config(app_id)
    payload = build_dashboard(db, config, days=days, bucket_hours=bucket_hours)
    atomic_write_json(Path(out), payload)
    db.close()
    print(json.dumps({"event": "export_dashboard", "app_id": app_id, "path": str(out), "entities": len(payload["entities"])}, ensure_ascii=False))
    return payload
