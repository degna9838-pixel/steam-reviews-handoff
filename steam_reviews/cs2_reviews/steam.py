from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

import httpx

from .config import Settings, get_game

LOG = logging.getLogger(__name__)
ENDPOINT = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"


class SteamError(RuntimeError):
    pass


class SteamClient:
    def __init__(self, settings: Settings, app_id: int = 730,
                 request_gate: Callable[[], None] | None = None, *, transport=None, sleep: Callable = time.sleep):
        self.app_id = get_game(app_id).app_id
        if request_gate is not None and not callable(request_gate):
            raise ValueError("request_gate must be callable")
        self.request_gate = request_gate
        self.settings = settings
        self.sleep = sleep
        self.client = httpx.Client(timeout=settings.request_timeout_seconds, transport=transport,
                                   headers={"User-Agent": "SteamReviewsCourseProject/1.3"}, follow_redirects=True,
                                   event_hooks={"request": [self._before_request]})

    def _before_request(self, request: httpx.Request) -> None:
        if self.request_gate is not None:
            self.request_gate()

    def close(self) -> None:
        self.client.close()

    def fetch(self, *, mode: str, cursor: str, start: int, end: int) -> dict:
        if mode not in {"recent", "updated"}:
            raise ValueError("Unknown collection mode")
        payload = {"appid": self.app_id, "filter": 1 if mode == "recent" else 2,
                   "languages": ["english"], "review_type": 0, "purchase_type": 1,
                   "num_per_page": 100, "filter_offtopic_activity": False,
                   "cursor": cursor, "date_range_start": start, "date_range_end": end}
        for attempt in range(self.settings.max_retries + 1):
            delay = min(60.0, 2.0 ** (attempt + 1))
            try:
                response = self.client.get(ENDPOINT, params={"input_json": json.dumps(payload, separators=(",", ":"))})
                if response.status_code == 429 or response.status_code >= 500:
                    try:
                        delay = max(delay, min(120.0, float(response.headers.get("Retry-After", delay))))
                    except ValueError:
                        pass
                    raise SteamError(f"Steam HTTP {response.status_code}")
                response.raise_for_status()
                try:
                    envelope = response.json()
                except ValueError as exc:
                    raise SteamError("Steam returned invalid JSON") from exc
                body = envelope.get("response") if isinstance(envelope, dict) else None
                if not isinstance(body, dict):
                    raise SteamError("Steam response schema is invalid; checkpoint retained")
                # Steam omits the repeated reviews field on real terminal pages.
                # Accept omission only with an explicit zero page count, never by default.
                summary = body.get("query_summary")
                if "reviews" not in body and isinstance(summary, dict) and summary.get("num_reviews") == 0:
                    body["reviews"] = []
                if not isinstance(body.get("reviews"), list):
                    raise SteamError("Steam response schema is invalid; checkpoint retained")
                if isinstance(summary, dict) and "num_reviews" in summary and summary["num_reviews"] != len(body["reviews"]):
                    raise SteamError("Steam page count does not match review array")
                if body["reviews"] and (not isinstance(body.get("cursor"), str) or not body["cursor"]):
                    raise SteamError("Non-empty review page has no cursor")
                return body
            except httpx.HTTPStatusError as exc:
                # Permanent request errors are not retried indefinitely.
                raise SteamError(f"Steam HTTP {exc.response.status_code}") from exc
            except (httpx.RequestError, SteamError) as exc:
                if attempt >= self.settings.max_retries:
                    raise SteamError(f"Steam request failed after {attempt + 1} attempt(s): {exc}") from exc
                LOG.warning("Steam request retry %d in %.1fs: %s", attempt + 1, delay, exc)
                self.sleep(delay)
        raise AssertionError("Unreachable")
