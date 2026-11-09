"""Cleaning upgrades. Call while holding CollectorLock."""
from __future__ import annotations

import uuid

from .storage import Store, utc_now


def ensure_current_cleaning(store: Store, *, force: bool = False) -> dict | None:
    from .cleaning import PIPELINE_VERSION
    unknown = set(store.pipeline_versions()) - {"clean-en-v1", "clean-en-v2", PIPELINE_VERSION}
    if unknown:
        raise ValueError(f"Unknown stored cleaning version(s): {sorted(unknown)}; refusing an implicit downgrade")
    if not force and not store.needs_reprocess():
        return None
    stamp = utc_now().replace(":", "").replace("-", "")
    backup = store.path.parent / "backups" / f"{store.path.stem}_before_reprocess_{stamp}_{uuid.uuid4().hex[:8]}.sqlite3"
    store.backup(backup)
    result = store.reprocess()
    target = store.path.with_name("sample_reviews_current.jsonl" if store.path.name == "sample.sqlite3" else "reviews_current.jsonl")
    store.export(target)
    return {**result, "backup": str(backup), "output": str(target),
            "manifest": str(target.with_suffix(".manifest.json"))}
