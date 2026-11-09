
import unittest
from unittest.mock import patch

from trend_analysis.consumer import sync_one


class TestTrendConsumer(unittest.TestCase):

    @patch("trend_analysis.consumer.subprocess.run")
    def test_sync_calls_existing_consumer(self, mock_run):
        sync_one(
            app_id=730,
            consumer_name="trend_demo",
        )

        mock_run.assert_called_once()

        args, kwargs = mock_run.call_args
        command = args[0]

        self.assertIn("consume_api.py", command[1])
        self.assertIn("--once", command)
        self.assertIn("--app-id", command)
        self.assertIn("730", command)
        self.assertIn("trend_demo", command)
        self.assertTrue(kwargs["check"])

    def test_invalid_game_is_rejected(self):
        with self.assertRaises(ValueError):
            sync_one(
                app_id=999,
                consumer_name="trend_demo",
            )

    def test_invalid_consumer_name_is_rejected(self):
        with self.assertRaises(ValueError):
            sync_one(
                app_id=730,
                consumer_name="../invalid",
            )


if __name__ == "__main__":
    unittest.main()
