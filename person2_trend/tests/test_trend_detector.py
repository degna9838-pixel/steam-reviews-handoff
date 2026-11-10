import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trend_analysis.detector import (
    detect_from_windows,
    detect_trends,
    term_strength,
)
from trend_analysis.experiment import FIXTURE_END, build_fixture


def _window(app_id, start, end, reviews, keywords):
    return {
        "app_id": app_id,
        "consumer_name": "unit",
        "start_time": start,
        "end_time": end,
        "stored_reviews_in_window": reviews,
        "total_reviews": reviews,
        "keywords": keywords,
    }


class TestStrength(unittest.TestCase):
    def test_zero_baseline_stays_finite(self):
        strength = term_strength(1.0, 0.0, smoothing=0.05, max_strength=20)
        self.assertLessEqual(strength, 20)
        self.assertGreater(strength, 1)

    def test_equal_rates_stay_near_one(self):
        strength = term_strength(0.2, 0.2, smoothing=0.05, max_strength=20)
        self.assertAlmostEqual(strength, 1.0)


class TestWindows(unittest.TestCase):
    def test_rejects_overlapping_or_mixed_games(self):
        recent = _window(730, "2026-11-10T11:00:00+00:00", "2026-11-10T12:00:00+00:00", 20, [])
        overlap = _window(730, "2026-11-10T10:00:00+00:00", "2026-11-10T11:30:00+00:00", 30, [])
        with self.assertRaises(ValueError):
            detect_from_windows(recent, overlap)
        other = _window(570, "2026-11-09T11:00:00+00:00", "2026-11-10T11:00:00+00:00", 30, [])
        with self.assertRaises(ValueError):
            detect_from_windows(recent, other)


class TestFixtureDetection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_root = Path(self.temp.name)
        build_fixture(self.data_root, "trend_fixture", FIXTURE_END)
        self.result = detect_trends(
            730,
            consumer_name="trend_fixture",
            end=FIXTURE_END,
            data_root=self.data_root,
            computed_at=FIXTURE_END,
        )
        self.terms = {item["term"]: item for item in self.result["trends"]}

    def test_windows_do_not_overlap_and_cover_one_and_twenty_four_hours(self):
        recent_start = datetime.fromisoformat(self.result["recent_start"])
        recent_end = datetime.fromisoformat(self.result["recent_end"])
        baseline_start = datetime.fromisoformat(self.result["baseline_start"])
        baseline_end = datetime.fromisoformat(self.result["baseline_end"])
        self.assertEqual(baseline_end, recent_start)
        self.assertEqual(recent_end - recent_start, timedelta(hours=1))
        self.assertEqual(baseline_end - baseline_start, timedelta(hours=24))
        self.assertEqual(self.result["recent_reviews"], 20)
        self.assertEqual(self.result["baseline_reviews"], 40)

    def test_surge_stable_new_and_one_off_terms(self):
        self.assertEqual(self.result["data_status"], "ready")
        self.assertTrue(self.result["conclusions_publishable"])
        self.assertIn("lag", self.result["trending_terms"])
        self.assertIn("vac", self.result["trending_terms"])
        self.assertNotIn("server", self.result["trending_terms"])
        self.assertNotIn("gg", self.result["trending_terms"])
        self.assertNotIn("spam", self.terms)
        self.assertNotIn("play", self.terms)

        lag = self.terms["lag"]
        self.assertEqual(lag["recent_count"], 12)
        self.assertEqual(lag["baseline_count"], 4)
        self.assertEqual(lag["recent_tf"], 12)
        self.assertFalse(lag["is_new"])
        self.assertAlmostEqual(lag["strength"], 4.333333, places=5)

        server = self.terms["server"]
        self.assertEqual(server["recent_count"], 9)
        self.assertEqual(server["baseline_count"], 20)
        self.assertLess(server["strength"], 2)
        self.assertFalse(server["is_trending"])

        vac = self.terms["vac"]
        self.assertTrue(vac["is_new"])
        self.assertEqual(vac["baseline_count"], 0)
        self.assertEqual(vac["strength"], 7.0)
        self.assertLessEqual(vac["strength"], 20)

        gg = self.terms["gg"]
        self.assertTrue(gg["is_new"])
        self.assertFalse(gg["meets_support"])
        self.assertFalse(gg["is_trending"])

    def test_other_game_is_not_mixed_into_cs2(self):
        self.assertNotIn("roshan", self.terms)
        dota = detect_trends(
            570,
            consumer_name="trend_fixture",
            end=FIXTURE_END,
            data_root=self.data_root,
            computed_at=FIXTURE_END,
        )
        self.assertEqual(dota["app_id"], 570)
        self.assertEqual([item["term"] for item in dota["trends"]], ["roshan"])
        self.assertEqual(dota["data_status"], "sparse_recent")
        self.assertFalse(dota["conclusions_publishable"])
        self.assertEqual(dota["trending_terms"], [])

    def test_offline_source_does_not_publish_even_when_the_score_is_high(self):
        offline = detect_trends(
            730,
            consumer_name="trend_fixture",
            end=FIXTURE_END,
            data_root=self.data_root,
            computed_at=FIXTURE_END,
            sync_status={"mode": "offline", "ready_for_provisional_analysis": False, "sync_status": "offline_sample"},
        )
        self.assertEqual(offline["data_status"], "offline_sample")
        self.assertFalse(offline["conclusions_publishable"])
        self.assertEqual(offline["trending_terms"], [])
        self.assertGreater(offline["trends"][0]["strength"], 2)

    def test_naive_end_time_is_rejected(self):
        with self.assertRaises(ValueError):
            detect_trends(
                730,
                consumer_name="trend_fixture",
                end=datetime(2026, 11, 10, 12, 0),
                data_root=self.data_root,
            )


if __name__ == "__main__":
    unittest.main()
