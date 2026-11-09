from __future__ import annotations

from dataclasses import replace

import pytest

from sentiment_analysis.entities import load_entity_config
from sentiment_analysis.sentiment import SentimentAnalyzer
from sentiment_analysis.settings import ALGORITHM_VERSION
from sentiment_analysis.store import (
    analysis_rows,
    bind_state,
    connect,
    ensure_algorithm_binding,
    init_schema,
    rebuild,
    save_batch,
    state_get,
)

from .helpers import batch, record, setup


class CountingAnalyzer(SentimentAnalyzer):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def score_match(self, match):
        self.calls += 1
        return super().score_match(match)


class FailingAnalyzer(SentimentAnalyzer):
    def score_match(self, match):
        raise RuntimeError("analysis failed")


def test_save_batch_writes_cursor_analysis_and_normalizes_time(tmp_path):
    db, config, analyzer = setup(tmp_path)
    item = record(created_at="2026-11-09T00:00:00+00:00")
    cursor, changed = save_batch(db, batch(item), 0, app_id=730, config=config,
                                 analyzer=analyzer, algorithm_version=ALGORITHM_VERSION)
    assert (cursor, changed) == (1, 1)
    assert state_get(db, "cursor") == "1"
    rows = analysis_rows(db, ALGORITHM_VERSION)
    assert {row["entity_id"] for row in rows} == {"game", "anticheat"}
    assert {row["created_at"] for row in rows} == {"2026-11-09T00:00:00Z"}


def test_vote_only_revision_reuses_analysis(tmp_path):
    db, config, analyzer = setup(tmp_path)
    counting = CountingAnalyzer()
    save_batch(db, batch(record()), 0, app_id=730, config=config,
               analyzer=counting, algorithm_version=ALGORITHM_VERSION)
    first_calls = counting.calls
    assert first_calls > 0
    counting.calls = 0
    cursor, changed = save_batch(
        db, batch(record(revision=2, votes_up=99), change_id=2), 1,
        app_id=730, config=config, analyzer=counting, algorithm_version=ALGORITHM_VERSION,
    )
    assert (cursor, changed) == (2, 1)
    assert counting.calls == 0
    rows = db.execute("SELECT revision,reused_from_revision FROM analysis").fetchall()
    assert {tuple(row) for row in rows} == {(2, 1)}


def test_edited_text_recomputes_and_retracts_old_entity(tmp_path):
    db, config, analyzer = setup(tmp_path)
    save_batch(db, batch(record()), 0, app_id=730, config=config,
               analyzer=analyzer, algorithm_version=ALGORITHM_VERSION)
    cursor, changed = save_batch(
        db, batch(record(revision=2, text="The skins look great."), change_id=2), 1,
        app_id=730, config=config, analyzer=analyzer, algorithm_version=ALGORITHM_VERSION,
    )
    assert (cursor, changed) == (2, 1)
    rows = {row["entity_id"]: row for row in analysis_rows(db, ALGORITHM_VERSION)}
    assert "anticheat" not in rows
    assert rows["skins_economy"]["sentiment_label"] == "positive"
    assert rows["skins_economy"]["reused_from_revision"] is None


def test_unusable_revision_retracts_all_contributions(tmp_path):
    db, config, analyzer = setup(tmp_path)
    save_batch(db, batch(record()), 0, app_id=730, config=config,
               analyzer=analyzer, algorithm_version=ALGORITHM_VERSION)
    save_batch(
        db, batch(record(revision=2, usable=False), change_id=2), 1,
        app_id=730, config=config, analyzer=analyzer, algorithm_version=ALGORITHM_VERSION,
    )
    assert db.execute("SELECT COUNT(*) FROM analysis").fetchone()[0] == 0
    status = db.execute("SELECT status FROM review_status WHERE review_id='123'").fetchone()
    assert status["status"] == "excluded_unusable"


def test_empty_usable_text_is_skipped_not_neutralized(tmp_path):
    db, config, analyzer = setup(tmp_path)
    save_batch(db, batch(record(text="   ")), 0, app_id=730, config=config,
               analyzer=analyzer, algorithm_version=ALGORITHM_VERSION)
    assert db.execute("SELECT COUNT(*) FROM analysis").fetchone()[0] == 0
    status = db.execute("SELECT status FROM review_status WHERE review_id='123'").fetchone()
    assert status["status"] == "skipped_empty"


def test_failed_analysis_rolls_back_cursor_and_rows(tmp_path):
    db, config, analyzer = setup(tmp_path)
    save_batch(db, batch(record()), 0, app_id=730, config=config,
               analyzer=analyzer, algorithm_version=ALGORITHM_VERSION)
    before = db.execute("SELECT COUNT(*) FROM analysis").fetchone()[0]
    with pytest.raises(RuntimeError, match="analysis failed"):
        save_batch(
            db, batch(record(revision=2, text="VAC is great."), change_id=2), 1,
            app_id=730, config=config, analyzer=FailingAnalyzer(), algorithm_version=ALGORITHM_VERSION,
        )
    assert state_get(db, "cursor") == "1"
    assert db.execute("SELECT COUNT(*) FROM analysis").fetchone()[0] == before
    assert db.execute("SELECT revision FROM reviews WHERE review_id='123'").fetchone()[0] == 1


def test_stream_mismatch_stops_without_advancing(tmp_path):
    db, config, analyzer = setup(tmp_path)
    with pytest.raises(ValueError, match="Stream"):
        save_batch(db, batch(record(), stream="other"), 0, app_id=730, config=config,
                   analyzer=analyzer, algorithm_version=ALGORITHM_VERSION)
    assert state_get(db, "cursor") == "0"


def test_unknown_pipeline_stops_without_advancing(tmp_path):
    db, config, analyzer = setup(tmp_path)
    with pytest.raises(ValueError, match="Unsupported"):
        save_batch(db, batch(record(pipeline_version="future-v99")), 0, app_id=730,
                   config=config, analyzer=analyzer, algorithm_version=ALGORITHM_VERSION)
    assert state_get(db, "cursor") == "0"


def test_algorithm_or_config_change_requires_rebuild(tmp_path):
    db, config, analyzer = setup(tmp_path)
    with pytest.raises(ValueError, match="Algorithm"):
        ensure_algorithm_binding(db, config, "sentiment-v2")
    with pytest.raises(ValueError, match="Entity config"):
        ensure_algorithm_binding(db, replace(config, content_hash="different"), ALGORITHM_VERSION)


def test_rebuild_recomputes_from_local_mirror(tmp_path):
    db, config, analyzer = setup(tmp_path)
    save_batch(db, batch(record()), 0, app_id=730, config=config,
               analyzer=analyzer, algorithm_version=ALGORITHM_VERSION)
    count = rebuild(db, app_id=730, config=config, analyzer=analyzer,
                    algorithm_version=ALGORITHM_VERSION)
    assert count == 1
    assert db.execute("SELECT COUNT(*) FROM analysis").fetchone()[0] == 2


def test_duplicate_event_with_no_new_revision_is_idempotent(tmp_path):
    db, config, analyzer = setup(tmp_path)
    save_batch(db, batch(record()), 0, app_id=730, config=config,
               analyzer=analyzer, algorithm_version=ALGORITHM_VERSION)
    before = db.execute("SELECT COUNT(*) FROM analysis").fetchone()[0]
    cursor, changed = save_batch(db, batch(record(), change_id=2), 1, app_id=730,
                                 config=config, analyzer=analyzer,
                                 algorithm_version=ALGORITHM_VERSION)
    assert (cursor, changed) == (2, 0)
    assert db.execute("SELECT COUNT(*) FROM analysis").fetchone()[0] == before


def test_same_review_id_is_isolated_between_games(tmp_path):
    dbs = {}
    for app_id in (730, 570):
        db = connect(tmp_path / f"{app_id}.sqlite3")
        init_schema(db)
        bind_state(db, app_id, f"stream-{app_id}", "live")
        dbs[app_id] = (db, load_entity_config(app_id), SentimentAnalyzer())
    item730 = record(review_id="1", text="CS2 is fun.", app_id=730)
    item570 = record(review_id="1", text="Dota 2 is fun.", app_id=570)
    save_batch(dbs[730][0], batch(item730, stream="stream-730"), 0, app_id=730,
               config=dbs[730][1], analyzer=dbs[730][2], algorithm_version=ALGORITHM_VERSION)
    save_batch(dbs[570][0], batch(item570, stream="stream-570"), 0, app_id=570,
               config=dbs[570][1], analyzer=dbs[570][2], algorithm_version=ALGORITHM_VERSION)
    assert state_get(dbs[730][0], "stream_id") == "stream-730"
    assert state_get(dbs[570][0], "stream_id") == "stream-570"
    assert dbs[730][0].execute("SELECT COUNT(*) FROM analysis").fetchone()[0] == 1
    assert dbs[570][0].execute("SELECT COUNT(*) FROM analysis").fetchone()[0] == 1
    with pytest.raises(ValueError, match="app_id"):
        save_batch(dbs[730][0], batch(item570, change_id=2, stream="stream-730"), 1, app_id=730,
                   config=dbs[730][1], analyzer=dbs[730][2], algorithm_version=ALGORITHM_VERSION)
