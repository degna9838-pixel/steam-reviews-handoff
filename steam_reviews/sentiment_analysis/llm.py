from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .entities import EntityConfig
from .preprocess import entity_context, normalize_llm_text
from .settings import (
    LLM_ALGORITHM_VERSION,
    LLM_MODEL_DEFAULT,
    LLM_PROMPT_VERSION,
    validate_app_id,
)
from .store import state_set
from .util import iso_utc, parse_utc, utc_now

LABELS = {"positive", "negative", "neutral"}


def build_job(
    app_id: int,
    record: dict,
    entity,
    config: EntityConfig,
    model: str = LLM_MODEL_DEFAULT,
) -> dict:
    revision = int(record["revision"])
    return {
        "job_id": f"{app_id}:{record['review_id']}:{revision}:{entity.entity_id}:{LLM_PROMPT_VERSION}",
        "app_id": app_id,
        "review_id": record["review_id"],
        "revision": revision,
        "model": model,
        "prompt_version": LLM_PROMPT_VERSION,
        "entity_config_version": config.version,
        "pipeline_version": record["pipeline_version"],
        "created_at": record["created_at"],
        "text_clean": record["text_clean"],
        "entity": {
            "entity_id": entity.entity_id,
            "name": entity.name,
            "scope": entity.scope,
        },
    }


def build_context_job(
    app_id: int,
    record: dict,
    entity,
    config: EntityConfig,
    model: str = LLM_MODEL_DEFAULT,
    max_chars: int = 900,
) -> dict:
    job = build_job(app_id, record, entity, config, model)
    job["llm_input"] = entity_context(record.get("text_clean", ""), entity, max_chars)
    job["normalized_text"] = normalize_llm_text(record.get("text_clean", ""))
    job["entity"]["aliases"] = list(entity.aliases)
    return job


def export_jobs(
    db: sqlite3.Connection,
    config: EntityConfig,
    out: Path,
    *,
    app_id: int,
    model: str = LLM_MODEL_DEFAULT,
    include_labeled: bool = False,
    limit: int | None = None,
) -> int:
    validate_app_id(app_id)
    rows = db.execute(
        """
        SELECT a.review_id,a.revision,a.entity_id,r.record_json
        FROM analysis a
        JOIN review_status s ON s.review_id=a.review_id AND s.revision=a.revision
        JOIN reviews r ON r.review_id=a.review_id
        WHERE s.status='analyzed'
        ORDER BY a.created_at,a.review_id,a.entity_id
        """
    ).fetchall()
    jobs = []
    for row in rows:
        record = json.loads(row["record_json"])
        if int(record["revision"]) != int(row["revision"]):
            raise ValueError("Stored review/revision mismatch")
        if not include_labeled:
            labeled = db.execute(
                "SELECT 1 FROM llm_analysis WHERE review_id=? AND revision=? AND entity_id=? LIMIT 1",
                (record["review_id"], record["revision"], row["entity_id"]),
            ).fetchone()
            if labeled:
                continue
        entity = config.by_id(row["entity_id"])
        jobs.append(build_job(app_id, record, entity, config, model))
        if limit is not None and len(jobs) >= limit:
            break
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as stream:
        for job in jobs:
            stream.write(json.dumps(job, ensure_ascii=False) + "\n")
    return len(jobs)


def import_predictions(
    db: sqlite3.Connection,
    config: EntityConfig,
    predictions: Path,
    *,
    app_id: int,
    model: str = LLM_MODEL_DEFAULT,
    prompt_version: str = LLM_PROMPT_VERSION,
) -> dict:
    validate_app_id(app_id)
    imported = 0
    skipped = 0
    errors = []
    pending = []
    with open(predictions, "r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("app_id") != app_id:
                errors.append({"job_id": row.get("job_id"), "error": "app_id mismatch"})
                continue
            try:
                entity_id = row.get("entity_id")
                if entity_id is None and isinstance(row.get("entity"), dict):
                    entity_id = row["entity"].get("entity_id")
                if entity_id not in {entity.entity_id for entity in config.entities}:
                    raise ValueError(f"unknown entity_id {entity_id!r}")
                label = row.get("label")
                if label not in LABELS:
                    raise ValueError(f"invalid label {label!r}")
                review_row = db.execute(
                    "SELECT record_json,revision FROM reviews WHERE review_id=?",
                    (row.get("review_id"),),
                ).fetchone()
                if review_row is None:
                    raise ValueError("review_id not in local mirror")
                record = json.loads(review_row["record_json"])
                if int(record["revision"]) != int(row.get("revision", -1)):
                    skipped += 1
                    continue
                pending.append((record, entity_id, label, str(row.get("reason") or "")[:500], row))
            except Exception as exc:
                errors.append({"job_id": row.get("job_id"), "error": str(exc)})
    now_text = iso_utc(utc_now())
    with db:
        for record, entity_id, label, reason, row in pending:
            review_id = record["review_id"]
            revision = int(record["revision"])
            created_text = iso_utc(parse_utc(record["created_at"]))
            db.execute(
                "DELETE FROM llm_analysis WHERE review_id=? AND entity_id=?",
                (review_id, entity_id),
            )
            raw = json.dumps({"label": label, "reason": reason}, ensure_ascii=False)
            db.execute(
                "INSERT INTO llm_analysis VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    review_id,
                    revision,
                    entity_id,
                    1,
                    label,
                    None,
                    reason,
                    created_text,
                    record["pipeline_version"],
                    LLM_ALGORITHM_VERSION,
                    config.version,
                    now_text,
                    model,
                    prompt_version,
                    raw,
                ),
            )
            imported += 1
        state_set(db, "llm_model", model)
        state_set(db, "llm_prompt_version", prompt_version)
        state_set(db, "llm_labeled_at", now_text)
    return {
        "jobs_read": imported + skipped + len(errors),
        "imported": imported,
        "skipped_stale": skipped,
        "errors": len(errors),
        "error_samples": errors[:10],
    }
