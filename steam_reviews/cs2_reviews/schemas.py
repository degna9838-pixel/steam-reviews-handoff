"""Explicit response contract, also displayed in the generated /docs page."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReviewRecord(Contract):
    schema_version: Literal["1.0"]
    source: Literal["steam"]
    app_id: int = Field(gt=0, description="Steam App ID; select a matching game-specific stream")
    review_id: str = Field(description="Steam recommendation ID; use as a stable string key")
    created_at: str = Field(description="Publication time, UTC ISO 8601; use for publication windows")
    updated_at: str = Field(description="Source modification time, UTC ISO 8601")
    first_seen_at: str
    last_seen_at: str
    language: str
    language_check: Literal["passed", "uncertain", "rejected"]
    text_raw: str
    text_clean: str = Field(description="Semantic-preserving text for sentiment/entity analysis")
    tokens_trend: list[str] = Field(description="Ordered tokens, including repetitions, for trend analysis")
    steam_recommended: bool = Field(description="Platform recommendation, not a text sentiment truth label")
    votes_up: int | None = Field(default=None, ge=0, description="Last observed helpful-vote count; null if unavailable, not the recommendation flag")
    comment_count: int | None = Field(default=None, ge=0, description="Last observed number of comments on this review; null if unavailable")
    quality_flags: list[str]
    usable_for_trend: bool
    usable_for_sentiment: bool
    pipeline_version: str
    revision: int = Field(ge=1)


class ChangeEvent(Contract):
    change_id: int = Field(ge=1)
    operation: Literal["upsert"]
    review_id: str
    revision: int = Field(ge=1)
    review: ReviewRecord


class ChangesPage(Contract):
    schema_version: Literal["1.0"]
    stream_id: str
    items: list[ChangeEvent]
    next_cursor: str
    has_more: bool


class CurrentPage(Contract):
    schema_version: Literal["1.0"]
    stream_id: str
    items: list[ReviewRecord]
    next_after_id: int
    has_more: bool
    snapshot_cursor: str
