from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Literal

from fastapi import FastAPI, HTTPException, Query

from . import __version__
from .config import GAMES, Settings, get_game
from .coordinator import MultiGameCollector
from .schemas import ChangesPage, CurrentPage, ReviewRecord
from .storage import Store, utc_now


def create_app(store: Store, settings: Settings | None = None, *, live: bool = False,
               stores: dict[int, Store] | None = None) -> FastAPI:
    settings = settings or Settings()
    selected = dict(stores) if stores is not None else {store.app_id: store}
    if not selected or any(key != value.app_id for key, value in selected.items()):
        raise ValueError("Store map keys must match each database app_id")
    runner = MultiGameCollector(selected, settings) if live else None

    def resolve(app_id: int) -> Store:
        try:
            get_game(app_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if app_id not in selected:
            raise HTTPException(503, "This game is not enabled in this service")
        return selected[app_id]

    @asynccontextmanager
    async def lifespan(app):
        if runner:
            runner.start()
        try:
            yield
        finally:
            if runner:
                import asyncio
                await asyncio.to_thread(runner.stop)

    app = FastAPI(title="Steam English Reviews — Multi-game Local API", version=__version__, lifespan=lifespan,
                  description="Select app_id per game. Independent databases and streams; API requests only read local data.")

    @app.get("/v1/games")
    def games():
        items = []
        for game in GAMES.values():
            entry = {**asdict(game), "enabled": game.app_id in selected}
            if game.app_id in selected:
                stats = selected[game.app_id].stats()
                entry.update(review_count=stats["review_count"], stream_id=stats["stream_id"],
                             backfill_status=stats.get("backfill_status"), pipeline_versions=stats["pipeline_versions"])
            items.append(entry)
        return {"schema_version": "1.0", "mode": "live" if live else "offline", "games": items}

    @app.get("/v1/health")
    def health(app_id: int = Query(730, gt=0)):
        from .cleaning import PIPELINE_VERSION
        stats = resolve(app_id).stats()
        return {**stats, "service": "cs2-reviews", "version": __version__, "mode": "live" if live else "offline",
                "app_id": app_id, "game": asdict(get_game(app_id)), "ready": True,
                "data_ready": stats["review_count"] > 0,
                "collector_state": runner.state_for(app_id) if runner else "disabled", "checked_at": utc_now(),
                "pipeline_version": PIPELINE_VERSION,
                "cleaning_ready": set(stats["pipeline_versions"]) <= {PIPELINE_VERSION},
                "poll_seconds": settings.poll_seconds}

    @app.get("/v1/reviews/changes", response_model=ChangesPage)
    def changes(cursor: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000), app_id: int = Query(730, gt=0)):
        target = resolve(app_id)
        try:
            return target.changes(cursor, limit)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/reviews", response_model=CurrentPage)
    def reviews(after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
                usable_for: Literal["trend", "sentiment"] | None = None, app_id: int = Query(730, gt=0)):
        return resolve(app_id).current(after_id, limit, usable_for)

    @app.get("/v1/reviews/{review_id}", response_model=ReviewRecord)
    def single(review_id: str, app_id: int = Query(730, gt=0)):
        record = resolve(app_id).review(review_id)
        if record is None:
            raise HTTPException(404, "Review not found")
        return record

    return app
