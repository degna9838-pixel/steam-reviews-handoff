from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import time
from pathlib import Path

from .entities import load_entity_config
from .llm import export_jobs, import_predictions
from .settings import (
    LLM_ACTIVE_PROMPT_VERSION,
    LLM_MODEL_DEFAULT,
    LIVE_CONSUMER,
    ROOT,
    SUPPORTED_APP_IDS,
    analysis_db_path,
    sentiment_dir,
    validate_app_id,
)
from .store import connect, init_schema
from .util import iso_utc, utc_now


def to_wsl_path(path: Path) -> str:
    resolved = Path(path).resolve()
    drive = resolved.drive
    if len(drive) != 2 or drive[1] != ":":
        raise ValueError(f"Only Windows drive paths are supported: {resolved}")
    rest = resolved.as_posix().split(":", 1)[1].lstrip("/")
    return f"/mnt/{drive[0].lower()}/{rest}"


def build_wsl_command(
    *,
    project_root: Path,
    jobs_path: Path,
    predictions_path: Path,
    distro: str,
    user: str | None,
    workers: int,
    batch_size: int,
    max_batch_chars: int,
    max_text_chars: int,
    prompt_style: str,
) -> list[str]:
    root = shlex.quote(to_wsl_path(project_root))
    jobs = shlex.quote(to_wsl_path(jobs_path))
    predictions = shlex.quote(to_wsl_path(predictions_path))
    script = (
        f"cd {root} && bash sentiment_analysis/calibration/run_cityu_llm.sh "
        f"--input {jobs} --output {predictions} "
        f"--workers {workers} --batch-size {batch_size} "
        f"--max-batch-chars {max_batch_chars} --max-text-chars {max_text_chars} "
        f"--prompt-style {prompt_style} --sleep 0.05"
    )
    command = ["wsl.exe", "-d", distro]
    if user:
        command += ["-u", user]
    command += ["--", "bash", "-c", script]
    return command


def refresh_game(app_id: int, args, *, echo=print) -> int:
    validate_app_id(app_id)
    db_path = analysis_db_path(app_id, args.consumer)
    if not db_path.exists():
        echo(f"[{iso_utc(utc_now())}] app={app_id} skip: analysis database not found")
        return 0
    config = load_entity_config(app_id)
    llm_dir = sentiment_dir(app_id, args.consumer) / "llm"
    llm_dir.mkdir(parents=True, exist_ok=True)
    jobs_path = llm_dir / "pending_jobs.jsonl"
    predictions_path = llm_dir / "pending_predictions.jsonl"
    db = connect(db_path)
    try:
        ready = db.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name IN ('llm_analysis','effective_analysis')"
        ).fetchall()
        if len({row[0] for row in ready}) < 2:
            init_schema(db)
        count = export_jobs(
            db, config, jobs_path,
            app_id=app_id, model=args.model, include_labeled=False,
        )
        if count == 0:
            echo(f"[{iso_utc(utc_now())}] app={app_id} no pending LLM rows")
            return 0
        command = build_wsl_command(
            project_root=ROOT,
            jobs_path=jobs_path,
            predictions_path=predictions_path,
            distro=args.wsl_distro,
            user=args.wsl_user,
            workers=args.workers,
            batch_size=args.batch_size,
            max_batch_chars=args.max_batch_chars,
            max_text_chars=args.max_text_chars,
            prompt_style=args.prompt_style,
        )
        echo(f"[{iso_utc(utc_now())}] app={app_id} labeling {count} pending rows")
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=args.timeout, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            detail = getattr(exc, "stderr", None) or str(exc)
            echo(f"[{iso_utc(utc_now())}] app={app_id} LLM batch failed: {str(detail)[-1000:]}")
            return 1
        if completed.stderr.strip():
            echo(f"[{iso_utc(utc_now())}] app={app_id} batch log (tail): {completed.stderr.strip()[-1000:]}")
        result = import_predictions(
            db, config, predictions_path,
            app_id=app_id, model=args.model, prompt_version=args.prompt_version,
        )
        echo(
            f"[{iso_utc(utc_now())}] app={app_id} imported={result['imported']} "
            f"skipped_stale={result['skipped_stale']} errors={result['errors']}"
        )
        return 0 if result["errors"] == 0 else 1
    finally:
        db.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Incrementally fill LLM sentiment labels for new reviews")
    parser.add_argument("--app-id", type=int, action="append", dest="app_ids")
    parser.add_argument("--consumer", default=LIVE_CONSUMER)
    parser.add_argument("--interval", type=float, default=180.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--max-batch-chars", type=int, default=30000)
    parser.add_argument("--max-text-chars", type=int, default=600)
    parser.add_argument("--prompt-style", default="simple_overall")
    parser.add_argument("--model", default=LLM_MODEL_DEFAULT)
    parser.add_argument("--prompt-version", default=LLM_ACTIVE_PROMPT_VERSION)
    parser.add_argument("--wsl-distro", default=os.environ.get("SENTIMENT_WSL_DISTRO", "Ubuntu-24.04"))
    parser.add_argument("--wsl-user", default=os.environ.get("SENTIMENT_WSL_USER") or None)
    parser.add_argument("--timeout", type=float, default=3600.0)
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.workers <= 0 or args.batch_size <= 0:
        parser.error("--workers and --batch-size must be positive")
    app_ids = tuple(args.app_ids) if args.app_ids else SUPPORTED_APP_IDS
    for app_id in app_ids:
        try:
            validate_app_id(app_id)
        except ValueError as exc:
            parser.error(str(exc))
    try:
        while True:
            for app_id in app_ids:
                try:
                    refresh_game(app_id, args)
                except Exception as exc:
                    print(f"[{iso_utc(utc_now())}] app={app_id} refresh error: {exc}", flush=True)
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
