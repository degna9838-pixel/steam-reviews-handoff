from __future__ import annotations

import re
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
ROOT = PACKAGE_ROOT.parent
ANALYSIS_ROOT = ROOT / "analysis"
CONFIG_ROOT = PACKAGE_ROOT / "configs"
LEXICON_PATH = PACKAGE_ROOT / "domain_lexicon.json"

SUPPORTED_PIPELINES = {"clean-en-v1", "clean-en-v2", "clean-en-v3"}
CURRENT_PIPELINE = "clean-en-v3"
ALGORITHM_NAME = "vader-domain"
ALGORITHM_VERSION = "sentiment-v1"
LLM_ALGORITHM_NAME = "llm-entity"
LLM_ALGORITHM_VERSION = "sentiment-llm-v1"
LLM_PROMPT_VERSION = "llm-entity-v1"
LLM_ACTIVE_PROMPT_VERSION = "llm-entity-v2-overall"
LLM_MODEL_DEFAULT = "CS5351/DeepSeek-V4-Flash"
SUMMARY_SCHEMA_VERSION = "sentiment-summary-1.0"
DASHBOARD_SCHEMA_VERSION = "sentiment-dashboard-1.0"
SAMPLE_SCHEMA_VERSION = "sentiment-samples-1.0"

DEFAULT_WINDOW_HOURS = 1
DEFAULT_BASELINE_HOURS = 24
DEFAULT_MIN_MENTIONS = 5
DEFAULT_CHANGE_THRESHOLD = 0.15
DEFAULT_ALPHA = 0.05
DEFAULT_POLL_SECONDS = 60.0
DEFAULT_LIMIT = 100

LIVE_CONSUMER = "sentiment_algorithm_v1"
SAMPLE_CONSUMER = "sentiment_demo"

SUPPORTED_APP_IDS = (730, 570, 4115450)
GAME_NAMES = {730: "CS2", 570: "Dota 2", 4115450: "Phantom Blade Zero"}


def validate_app_id(app_id: int) -> None:
    if type(app_id) is not int or app_id <= 0:
        raise ValueError("app_id must be a positive integer")
    if app_id not in SUPPORTED_APP_IDS:
        raise ValueError(f"Unsupported app_id {app_id}")


def validate_consumer_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name or ""):
        raise ValueError("Consumer name must be 1-64 letters/digits/_/-")
    return name


def game_dir(app_id: int) -> Path:
    validate_app_id(app_id)
    return ANALYSIS_ROOT / "games" / str(app_id)


def sentiment_dir(app_id: int, consumer: str) -> Path:
    validate_app_id(app_id)
    validate_consumer_name(consumer)
    return game_dir(app_id) / "sentiment" / consumer


def analysis_db_path(app_id: int, consumer: str) -> Path:
    return sentiment_dir(app_id, consumer) / "sentiment.sqlite3"


def summary_dir(app_id: int, consumer: str) -> Path:
    return sentiment_dir(app_id, consumer) / "summaries"


def log_dir(app_id: int, consumer: str) -> Path:
    return sentiment_dir(app_id, consumer) / "logs"


def entity_config_path(app_id: int) -> Path:
    validate_app_id(app_id)
    return CONFIG_ROOT / f"entities_{app_id}.json"
