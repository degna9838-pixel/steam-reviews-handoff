from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .steam import SteamClient
from .storage import Store, utc_now, unix_to_iso

LOG = logging.getLogger(__name__)


class CollectorBusy(RuntimeError):
    pass


class CollectorLock:
    """Advisory process lock, automatically released on process exit on both platforms."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.handle.close()
            self.handle = None
            raise CollectorBusy("Another collector is already using this database") from exc
        return self

    def __exit__(self, *args):
        if self.handle:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


class Collector:
    def __init__(self, store: Store, settings: Settings, client=None, *, clock=time.time, request_gate=None):
        self.store, self.settings = store, settings
        self.client = client or SteamClient(settings, app_id=store.app_id, request_gate=request_gate)
        self.clock = clock

    def close(self):
        self.client.close()

    def collect_once(self, stop: threading.Event | None = None) -> dict:
        """Call under CollectorLock. Commit review rows and page progress together."""
        stop = stop or threading.Event()
        now_unix = int(self.clock())
        scope = self.store.get("scope_start_unix")
        if scope is None:
            scope = max(0, now_unix - self.settings.backfill_days * 86400)
            self.store.set_many({"scope_start_unix": scope, "backfill_status": "pending",
                                 "backfill_target_days": self.settings.backfill_days,
                                 "backfill_requested_at": utc_now()})
        if self.store.get("backfill_status") == "pending":
            self.store.set_many({"backfill_status": "running", "backfill_started_at": utc_now()})
        self.store.set("last_attempt_at", utc_now())
        # Periodically reconcile the full fixed project scope for late-visible data.
        last_reconcile = self.store.get("last_reconcile_unix")
        if (last_reconcile is not None and now_unix - last_reconcile >= 86400
                and self.store.get("next_mode", "recent") == "recent"
                and not self.store.get("checkpoint_recent") and not self.store.get("checkpoint_updated")):
            for name in ("recent", "updated"):
                self.store.set(f"watermark_{name}", scope)
            self.store.set("reconcile_pending", True)
        totals = {"pages": 0, "received": 0, "inserted": 0, "updated": 0, "unchanged": 0}
        try:
            mode = self.store.get("next_mode", "recent")
            finished_updated = False
            for _ in range(2):
                key = f"checkpoint_{mode}"
                job = self.store.get(key)
                if not job:
                    watermark = self.store.get(f"watermark_{mode}", scope)
                    job = {"cursor": "*", "end": now_unix,
                           "start": max(scope, watermark - self.settings.overlap_seconds) if mode == "recent" else scope,
                           "updated_cutoff": max(scope, watermark - self.settings.overlap_seconds),
                           "pages_done": 0, "reviews_seen": 0}
                    self.store.set(key, job)
                while totals["pages"] < self.settings.max_pages_per_cycle and not stop.is_set():
                    page = self.client.fetch(mode=mode, cursor=job["cursor"], start=job["start"], end=job["end"])
                    raw = page["reviews"]
                    # Updated is ordered by update time, but API date filters refer to creation.
                    # Keep the fixed project creation scope and stop once updates pass the watermark.
                    selected = []
                    reached_cutoff = False
                    for review in raw:
                        if not isinstance(review, dict):
                            raise ValueError("Steam review is not an object")
                        created, updated = review.get("timestamp_created"), review.get("timestamp_updated")
                        if isinstance(created, bool) or not isinstance(created, (int, float)):
                            raise ValueError("Missing review creation timestamp")
                        if isinstance(updated, bool) or not isinstance(updated, (int, float)):
                            raise ValueError("Missing review update timestamp")
                        if mode == "updated" and updated < job["updated_cutoff"]:
                            reached_cutoff = True
                            continue
                        if scope <= created <= job["end"]:
                            selected.append(review)
                    next_cursor = page.get("cursor", "")
                    done = not raw or reached_cutoff
                    if not done and next_cursor == job["cursor"]:
                        raise RuntimeError("Steam pagination cursor did not advance; page checkpoint retained")
                    progressed_job = {**job, "cursor": next_cursor,
                                      "pages_done": job.get("pages_done", 0) + 1,
                                      "reviews_seen": job.get("reviews_seen", 0) + len(selected)}
                    state = {key: None, f"watermark_{mode}": job["end"],
                             "next_mode": "updated" if mode == "recent" else "recent"} if done else {key: progressed_job, "next_mode": mode}
                    state["active_scan"] = {"mode": mode, "start_at": unix_to_iso(job["start"]),
                                             "end_at": unix_to_iso(job["end"]),
                                             "pages_done": progressed_job["pages_done"],
                                             "reviews_seen": progressed_job["reviews_seen"],
                                             "total_matching": page.get("total_matching"), "complete": done}
                    counts = self.store.apply_page(selected, state=state)
                    totals["pages"] += 1
                    for name, value in counts.items():
                        totals[name] += value
                    LOG.info("%s page=%d received=%d inserted=%d updated=%d", mode, totals["pages"], counts["received"], counts["inserted"], counts["updated"])
                    self.store.set("last_page_at", utc_now())
                    if done:
                        if mode == "updated":
                            finished_updated = True
                        break
                    job = progressed_job
                    if stop.wait(self.settings.request_delay_seconds):
                        break
                if stop.is_set() or totals["pages"] >= self.settings.max_pages_per_cycle:
                    break
                if finished_updated:
                    break
                mode = self.store.get("next_mode", "recent")
            complete = finished_updated and not self.store.get("checkpoint_recent") and not self.store.get("checkpoint_updated")
            totals["sweep_complete"] = complete
            self.store.set("last_cycle", totals)
            self.store.set("last_success_at", utc_now())
            self.store.set("last_error", None)
            if complete:
                self.store.set("last_complete_sweep_at", utc_now())
                if self.store.get("backfill_status") in {"pending", "running"}:
                    self.store.set_many({"backfill_status": "completed", "backfill_completed_at": utc_now()})
                if last_reconcile is None or self.store.get("reconcile_pending"):
                    self.store.set("last_reconcile_unix", now_unix)
                    self.store.set("reconcile_pending", False)
            self.store.export(self.store.path.with_name("reviews_current.jsonl"))
            return totals
        except Exception as exc:
            self.store.set("last_error", {"at": utc_now(), "message": str(exc)})
            LOG.exception("Collection failed; successful pages and checkpoints are retained")
            raise


class BackgroundCollector:
    def __init__(self, store: Store, settings: Settings, *, request_gate=None):
        self.store, self.settings = store, settings
        self.stop_event = threading.Event()
        self.thread = None
        self.state = "not_started"
        self.request_gate = request_gate

    def start(self):
        self.thread = threading.Thread(target=self._run, name="steam-collector", daemon=True)
        self.thread.start()

    def _run(self):
        try:
            with CollectorLock(self.store.path.with_suffix(".lock")):
                from .maintenance import ensure_current_cleaning
                upgrade = ensure_current_cleaning(self.store)
                if upgrade:
                    LOG.info("Cleaning upgraded: %s", upgrade)
                self.state = "running"
                self.store.set_many({"collector_started_at": utc_now(), "collector_pid": os.getpid(),
                                     "schedule_kind": "start_to_start", "configured_poll_seconds": self.settings.poll_seconds})
                collector = (Collector(self.store, self.settings, request_gate=self.request_gate)
                             if self.request_gate is not None else Collector(self.store, self.settings))
                try:
                    while not self.stop_event.is_set():
                        started = time.monotonic()
                        self.store.set_many({"cycle_in_progress": True, "cycle_started_at": utc_now(), "next_run_at": None})
                        succeeded = False
                        try:
                            collector.collect_once(self.stop_event)
                            succeeded = True
                        except Exception:
                            pass  # Error detail is persisted by collect_once and exposed by /health.
                        incomplete = bool(self.store.get("checkpoint_recent") or self.store.get("checkpoint_updated"))
                        # Finish historical/catch-up pagination promptly instead of waiting ten
                        # minutes between every page-budget chunk. Failed requests back off normally.
                        catch_up = succeeded and incomplete and not self.stop_event.is_set()
                        delay = min(2.0, self.settings.request_delay_seconds) if catch_up else cycle_delay(self.settings.poll_seconds, time.monotonic() - started)
                        if not succeeded:
                            delay = max(30.0, delay)
                        self.store.set_many({"cycle_in_progress": False, "cycle_finished_at": utc_now(),
                                             "last_cycle_duration_seconds": round(time.monotonic() - started, 3),
                                             "schedule_phase": "catch_up" if catch_up else "polling",
                                             "next_run_at": unix_to_iso(time.time() + delay) if not self.stop_event.is_set() else None})
                        self.stop_event.wait(delay)
                finally:
                    self.store.set_many({"cycle_in_progress": False, "next_run_at": None,
                                         "collector_stopped_at": utc_now()})
                    collector.close()
        except CollectorBusy:
            self.state = "external_collector"
            LOG.warning("Another process owns the collector; API remains available")
        except Exception:
            self.state = "failed"
            LOG.exception("Background collector stopped unexpectedly")
        finally:
            if self.state == "running":
                self.state = "stopped"

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=self.settings.request_timeout_seconds + 5)


def cycle_delay(interval: float, elapsed: float) -> float:
    """Target start-to-start timing, with no concurrent or queued catch-up jobs."""
    return max(0.0, interval - max(0.0, elapsed))
