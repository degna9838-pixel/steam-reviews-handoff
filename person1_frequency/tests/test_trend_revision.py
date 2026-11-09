
"""Regression tests for latest-snapshot keyword calculations."""

import json
import sqlite3
import tempfile
import unittest

from contextlib import closing
from pathlib import Path

from trend_analysis.storage import get_reviews, get_top_keywords


START = "2026-11-09T00:00:00Z"
END = "2026-11-10T00:00:00Z"


class TestTrendRevision(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.data_root = Path(self.temp.name)
        self.db_path = (
            self.data_root / "consumer_revision_test.sqlite3"
        )

        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute(
                "CREATE TABLE reviews ("
                "review_id TEXT PRIMARY KEY, "
                "revision INTEGER NOT NULL, "
                "record_json TEXT NOT NULL)"
            )

    def make_review(self, revision, tokens, usable=True):
        return {
            "app_id": 730,
            "review_id": "review-001",
            "revision": revision,
            "created_at": "2026-11-09T12:00:00Z",
            "usable_for_trend": usable,
            "tokens_trend": tokens,
        }

    def save_snapshot(self, review):
        """Set up a test snapshot, not the upstream consumer."""

        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO reviews "
                "(review_id, revision, record_json) "
                "VALUES (?, ?, ?)",
                (
                    str(review["review_id"]),
                    review["revision"],
                    json.dumps(review),
                ),
            )

    def frequencies(self):
        result = get_top_keywords(
            app_id=730,
            start_time=START,
            end_time=END,
            top_n=None,
            consumer_name="revision_test",
            data_root=self.data_root,
        )

        terms = {
            item["term"]: item
            for item in result["keywords"]
        }

        return result, terms

    def test_new_revision_replaces_old_keywords(self):
        # Initial review: revision 1
        self.save_snapshot(
            self.make_review(1, ["server", "lag", "lag"])
        )

        before, old_terms = self.frequencies()

        self.assertEqual(before["total_reviews"], 1)
        self.assertEqual(old_terms["lag"]["tf"], 2)

        # Same review_id, but updated to revision 2
        self.save_snapshot(
            self.make_review(
                2, ["update", "improve", "improve"]
            )
        )

        after, new_terms = self.frequencies()

        rows = get_reviews(
            730,
            START,
            END,
            "revision_test",
            self.data_root,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["revision"], 2)
        self.assertEqual(after["stored_reviews_in_window"], 1)
        self.assertEqual(after["total_reviews"], 1)

        # Old keywords must disappear
        self.assertNotIn("server", new_terms)
        self.assertNotIn("lag", new_terms)

        # Only current keywords count
        self.assertEqual(new_terms["update"]["df"], 1)
        self.assertEqual(new_terms["improve"]["tf"], 2)
        self.assertEqual(new_terms["improve"]["df"], 1)

    def test_revision_becomes_unusable(self):
        # Initially usable
        self.save_snapshot(
            self.make_review(1, ["lag"])
        )

        # Same review becomes unusable
        self.save_snapshot(
            self.make_review(2, [], usable=False)
        )

        result, terms = self.frequencies()

        # Record still exists, but no TF/DF contribution
        self.assertEqual(
            result["stored_reviews_in_window"], 1
        )
        self.assertEqual(result["total_reviews"], 0)
        self.assertEqual(terms, {})

    def test_repeated_snapshot_does_not_inflate_frequency(self):
        review = self.make_review(
            2, ["update", "update"]
        )

        # Save current snapshot once
        self.save_snapshot(review)
        before, _ = self.frequencies()

        # Save identical snapshot again
        self.save_snapshot(review)
        after, terms = self.frequencies()

        with closing(sqlite3.connect(self.db_path)) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM reviews"
            ).fetchone()[0]

        self.assertEqual(count, 1)
        self.assertEqual(after, before)

        self.assertEqual(terms["update"]["tf"], 2)
        self.assertEqual(terms["update"]["df"], 1)


if __name__ == "__main__":
    unittest.main()
