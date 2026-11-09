import json
import sqlite3
import tempfile
import unittest

from contextlib import closing
from pathlib import Path

from trend_analysis.storage import get_reviews, get_top_keywords


class TestTrendStorage(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_root = Path(self.temp.name)

        records_730 = [
            ("r1", 1, "2026-11-01T00:00:00Z",
             ["lag", "lag", "server"], True),
            ("r2", 1, "2026-11-01T12:00:00Z",
             ["lag", "crash"], True),
            ("r3", 1, "2026-11-01T18:00:00Z",
             ["spam"], False),
            ("r4", 1, "2026-11-02T00:00:00Z",
             ["update"], True),
        ]

        records_570 = [
            ("r1", 1, "2026-11-01T12:00:00Z",
             ["dota", "match"], True),
        ]

        self._create_database(730, records_730)
        self._create_database(570, records_570)

    def _create_database(self, app_id, records):
        if app_id == 730:
            path = self.data_root / "consumer_trend_demo.sqlite3"
        else:
            path = (
                self.data_root / "games" / str(app_id)
                / "consumer_trend_demo.sqlite3"
            )

        path.parent.mkdir(parents=True, exist_ok=True)

        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                """
                CREATE TABLE reviews (
                    review_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    record_json TEXT NOT NULL
                )
                """
            )

            for review_id, revision, created_at, tokens, usable in records:
                review = {
                    "app_id": app_id,
                    "review_id": review_id,
                    "revision": revision,
                    "created_at": created_at,
                    "tokens_trend": tokens,
                    "usable_for_trend": usable,
                }

                db.execute(
                    "INSERT INTO reviews VALUES (?, ?, ?)",
                    (review_id, revision, json.dumps(review)),
                )

    def test_time_window_is_start_inclusive_end_exclusive(self):
        reviews = get_reviews(
            730,
            "2026-11-01T00:00:00Z",
            "2026-11-02T00:00:00Z",
            data_root=self.data_root,
        )

        self.assertEqual(
            [review["review_id"] for review in reviews],
            ["r1", "r2", "r3"],
        )

    def test_frequency_integration(self):
        result = get_top_keywords(
            730,
            "2026-11-01T00:00:00Z",
            "2026-11-02T00:00:00Z",
            data_root=self.data_root,
        )

        self.assertEqual(result["stored_reviews_in_window"], 3)
        self.assertEqual(result["total_reviews"], 2)

        terms = {item["term"]: item for item in result["keywords"]}

        self.assertEqual(terms["lag"]["tf"], 3)
        self.assertEqual(terms["lag"]["df"], 2)
        self.assertNotIn("spam", terms)
        self.assertNotIn("update", terms)

    def test_games_are_isolated(self):
        reviews = get_reviews(
            570,
            "2026-11-01T00:00:00Z",
            "2026-11-02T00:00:00Z",
            data_root=self.data_root,
        )

        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["app_id"], 570)
        self.assertEqual(reviews[0]["tokens_trend"], ["dota", "match"])

    def test_naive_datetime_is_rejected(self):
        with self.assertRaises(ValueError):
            get_reviews(
                730,
                "2026-11-01T00:00:00",
                "2026-11-02T00:00:00Z",
                data_root=self.data_root,
            )

    def test_missing_database_is_not_created(self):
        with self.assertRaises(FileNotFoundError):
            get_reviews(
                4115450,
                "2026-11-01T00:00:00Z",
                "2026-11-02T00:00:00Z",
                data_root=self.data_root,
            )


if __name__ == "__main__":
    unittest.main()
