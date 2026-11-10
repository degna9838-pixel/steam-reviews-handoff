import json
import tempfile
import unittest
from pathlib import Path

from trend_analysis.detector import detect_trends
from trend_analysis.experiment import FIXTURE_END, build_fixture
from trend_analysis.export import export_results


class TestExport(unittest.TestCase):
    def test_writes_per_game_and_combined_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "data"
            output = Path(temporary) / "analysis"
            build_fixture(data_root)
            result = detect_trends(
                730,
                consumer_name="trend_fixture",
                end=FIXTURE_END,
                data_root=data_root,
                computed_at=FIXTURE_END,
            )
            written = export_results([result], root=output)
            latest = output / "games" / "730" / "trends_latest.json"
            combined = output / "trends_latest.json"
            self.assertEqual(written["games"], [str(latest)])
            saved = json.loads(latest.read_text(encoding="utf-8"))
            self.assertEqual(saved["trending_terms"], ["vac", "lag"])
            bundle = json.loads(combined.read_text(encoding="utf-8"))
            self.assertEqual(bundle["games"][0]["app_id"], 730)
            self.assertTrue(any(output.joinpath("games", "730").glob("trends_2026*.json")))


if __name__ == "__main__":
    unittest.main()
