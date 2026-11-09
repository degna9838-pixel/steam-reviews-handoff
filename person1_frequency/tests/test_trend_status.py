
"""Tests for trend synchronization metadata."""

import sqlite3
import tempfile
import unittest

from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from trend_analysis.status import get_sync_status


class TestTrendStatus(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_root = Path(self.temp.name)

        path = self.data_root / "consumer_trend_demo.sqlite3"

        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)"
            )
            db.executemany(
                "INSERT INTO state (key, value) VALUES (?, ?)",
                [
                    ("app_id", "730"),
                    ("stream_id", "stream-a"),
                    ("cursor", "10"),
                ],
            )

    def make_health(
        self,
        stream_id="stream-a",
        latest_cursor="10",
        mode="offline",
        backfill_status=None,
        data_ready=True,
    ):
        return {
            "app_id": 730,
            "stream_id": stream_id,
            "latest_cursor": latest_cursor,
            "mode": mode,
            "data_ready": data_ready,
            "cleaning_ready": True,
            "backfill_status": backfill_status,
            "dataset_type": (
                "synthetic_examples_not_real_steam_reviews"
                if mode == "offline" else None
            ),
            "collector_state": (
                "disabled" if mode == "offline" else "running"
            ),
        }

    @patch("trend_analysis.status.httpx.get")
    def test_caught_up_offline_sample(self, mock_get):
        mock_get.return_value.json.return_value = self.make_health()

        result = get_sync_status(730, data_root=self.data_root)

        self.assertTrue(result["stream_matches"])
        self.assertTrue(result["caught_up"])
        self.assertEqual(result["sync_status"], "offline_sample")
        self.assertFalse(result["ready_for_provisional_analysis"])

    @patch("trend_analysis.status.httpx.get")
    def test_consumer_behind_upstream(self, mock_get):
        mock_get.return_value.json.return_value = self.make_health(
            latest_cursor="11"
        )

        result = get_sync_status(730, data_root=self.data_root)

        self.assertFalse(result["caught_up"])
        self.assertEqual(result["consumer_lag_events"], 1)
        self.assertEqual(
            result["sync_status"], "cursor_not_caught_up"
        )

    @patch("trend_analysis.status.httpx.get")
    def test_stream_mismatch(self, mock_get):
        mock_get.return_value.json.return_value = self.make_health(
            stream_id="different-stream"
        )

        result = get_sync_status(730, data_root=self.data_root)

        self.assertFalse(result["stream_matches"])
        self.assertEqual(result["sync_status"], "stream_mismatch")

    @patch("trend_analysis.status.httpx.get")
    def test_live_without_backfill_confirmation(self, mock_get):
        mock_get.return_value.json.return_value = self.make_health(
            mode="live"
        )

        result = get_sync_status(730, data_root=self.data_root)

        self.assertEqual(result["sync_status"], "backfill_not_verified")
        self.assertFalse(result["backfill_reported_complete"])
        self.assertFalse(result["ready_for_provisional_analysis"])

    @patch("trend_analysis.status.httpx.get")
    def test_backfill_in_progress(self, mock_get):
        mock_get.return_value.json.return_value = self.make_health(
            mode="live",
            backfill_status="running",
        )

        result = get_sync_status(730, data_root=self.data_root)

        self.assertEqual(result["sync_status"], "backfill_in_progress")
        self.assertFalse(result["ready_for_provisional_analysis"])

    @patch("trend_analysis.status.httpx.get")
    def test_completed_backfill(self, mock_get):
        mock_get.return_value.json.return_value = self.make_health(
            mode="live",
            backfill_status="completed",
        )

        result = get_sync_status(730, data_root=self.data_root)

        self.assertEqual(
            result["sync_status"],
            "live_synced_backfill_reported_complete",
        )
        self.assertTrue(result["backfill_reported_complete"])
        self.assertTrue(result["ready_for_provisional_analysis"])
        self.assertFalse(result["historical_completeness_verified"])

    @patch("trend_analysis.status.httpx.get")
    def test_not_ready_even_if_backfill_completed(self, mock_get):
        mock_get.return_value.json.return_value = self.make_health(
            mode="live",
            backfill_status="completed",
            data_ready=False,
        )

        result = get_sync_status(730, data_root=self.data_root)

        self.assertEqual(result["sync_status"], "upstream_not_ready")
        self.assertFalse(result["ready_for_provisional_analysis"])

    @patch("trend_analysis.status.httpx.get")
    def test_cursor_ahead_of_upstream(self, mock_get):
        mock_get.return_value.json.return_value = self.make_health(
            mode="live",
            latest_cursor="9",
            backfill_status="completed",
        )

        result = get_sync_status(730, data_root=self.data_root)

        self.assertEqual(
            result["sync_status"],
            "cursor_ahead_of_upstream",
        )
        self.assertFalse(result["ready_for_provisional_analysis"])


if __name__ == "__main__":
    unittest.main()
