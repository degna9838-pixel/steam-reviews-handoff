
import unittest

from trend_analysis.frequency import calculate_frequency


class TestFrequency(unittest.TestCase):

    def test_tf_df(self):
        reviews = [
            {
                "tokens_trend": ["lag", "lag", "server"],
                "usable_for_trend": True,
            },
            {
                "tokens_trend": ["lag", "crash"],
                "usable_for_trend": True,
            },
            {
                "tokens_trend": ["server", "crash"],
                "usable_for_trend": True,
            },
        ]

        result = calculate_frequency(reviews)

        self.assertEqual(result["total_reviews"], 3)

        terms = {
            item["term"]: item
            for item in result["keywords"]
        }

        self.assertEqual(terms["lag"]["tf"], 3)
        self.assertEqual(terms["lag"]["df"], 2)

        self.assertEqual(terms["server"]["tf"], 2)
        self.assertEqual(terms["server"]["df"], 2)

        self.assertEqual(terms["crash"]["tf"], 2)
        self.assertEqual(terms["crash"]["df"], 2)

        self.assertAlmostEqual(
            terms["lag"]["document_rate"], 2 / 3
        )

    def test_unusable_review_is_excluded(self):
        reviews = [
            {
                "tokens_trend": ["lag"],
                "usable_for_trend": True,
            },
            {
                "tokens_trend": ["spam", "spam"],
                "usable_for_trend": False,
            },
        ]

        result = calculate_frequency(reviews)

        self.assertEqual(result["total_reviews"], 1)
        self.assertEqual(
            [item["term"] for item in result["keywords"]],
            ["lag"],
        )

    def test_empty_input(self):
        result = calculate_frequency([])

        self.assertEqual(result["total_reviews"], 0)
        self.assertEqual(result["keywords"], [])

    def test_top_n(self):
        reviews = [
            {
                "tokens_trend": ["lag", "lag", "server"],
                "usable_for_trend": True,
            }
        ]

        result = calculate_frequency(reviews, top_n=1)

        self.assertEqual(len(result["keywords"]), 1)
        self.assertEqual(result["keywords"][0]["term"], "lag")

    def test_invalid_top_n(self):
        with self.assertRaises(ValueError):
            calculate_frequency([], top_n=0)


if __name__ == "__main__":
    unittest.main()
