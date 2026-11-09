from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Game:
    app_id: int
    name: str
    slug: str


GAMES = {
    730: Game(730, "CS2", "cs2"),
    570: Game(570, "Dota 2", "dota2"),
    4115450: Game(4115450, "影之刃零", "phantom-blade-zero"),
}


def get_game(app_id: int) -> Game:
    if type(app_id) is not int or app_id <= 0:
        raise ValueError("app_id must be a positive integer")
    if app_id not in GAMES:
        raise ValueError(f"Unsupported app_id {app_id}; supported IDs: {list(GAMES)}")
    return GAMES[app_id]


@dataclass(frozen=True)
class Settings:
    app_ids: tuple[int, ...] = (730, 570, 4115450)
    backfill_days: int = 30
    poll_seconds: int = 600
    request_delay_seconds: float = 1.0
    request_timeout_seconds: float = 30.0
    max_pages_per_cycle: int = 50
    overlap_seconds: int = 1200
    max_retries: int = 3
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        config_path = path or ROOT / "config.json"
        data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError("config.json must contain a JSON object")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unknown config keys: {sorted(unknown)}")
        if "app_ids" in data:
            values = data["app_ids"]
            if not isinstance(values, list) or not values:
                raise ValueError("app_ids must be a non-empty JSON list")
            app_ids = tuple(get_game(value).app_id for value in values)
            if len(set(app_ids)) != len(app_ids):
                raise ValueError("app_ids must not contain duplicates")
            data["app_ids"] = app_ids
        obj = cls(**data)
        for name in ("backfill_days", "poll_seconds", "max_pages_per_cycle", "overlap_seconds", "port"):
            value = getattr(obj, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not 1 <= obj.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if obj.host not in {"127.0.0.1", "localhost"}:
            raise ValueError("This local-only service must bind to 127.0.0.1 or localhost")
        for name in ("request_delay_seconds", "request_timeout_seconds"):
            value = getattr(obj, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive")
        if isinstance(obj.max_retries, bool) or not isinstance(obj.max_retries, int) or not 0 <= obj.max_retries <= 6:
            raise ValueError("max_retries must be an integer between 0 and 6")
        return obj


def db_path(sample: bool = False, app_id: int = 730) -> Path:
    get_game(app_id)
    directory = ROOT / "data"
    if app_id != 730:
        directory = directory / "games" / str(app_id)
    return directory / ("sample.sqlite3" if sample else "reviews.sqlite3")
