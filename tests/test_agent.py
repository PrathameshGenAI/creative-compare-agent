import json
import unittest

from creative_compare_agent import CreativeCompareAgent


class CreativeCompareAgentTests(unittest.TestCase):
    def test_compare_returns_all_required_criteria(self):
        agent = CreativeCompareAgent()
        card = agent.compare(
            "Try FreshBox today for simple family dinners.",
            "Win back weeknights with healthy 15-minute dinners for busy parents.",
            audience="busy parents",
            objective="trial signups",
        )
        criteria = {c.criterion for c in card.criteria}
        self.assertEqual(
            criteria,
            {
                "clarity",
                "originality",
                "audience_fit",
                "emotional_impact",
                "differentiation",
                "cta_strength",
                "risk_safety",
            },
        )
        self.assertGreaterEqual(card.weighted_total_a, 0)
        self.assertLessEqual(card.weighted_total_a, 10)
        self.assertGreaterEqual(card.weighted_total_b, 0)
        self.assertLessEqual(card.weighted_total_b, 10)
        self.assertTrue(card.recommendation)

    def test_json_output_is_serializable(self):
        agent = CreativeCompareAgent()
        card = agent.compare("Try our app today.", "Get calm mornings with a simple app.", audience="parents")
        parsed = json.loads(agent.to_json(card))
        self.assertIn("criteria", parsed)
        self.assertIn("weighted_total_a", parsed)

    def test_markdown_output_contains_table_and_recommendation(self):
        agent = CreativeCompareAgent()
        card = agent.compare("Try our app today.", "Get calm mornings with a simple app.", audience="parents")
        md = agent.to_markdown(card)
        self.assertIn("# Creative Compare Scorecard", md)
        self.assertIn("| Criterion |", md)
        self.assertIn("**Recommendation:**", md)

    def test_empty_creative_rejected(self):
        agent = CreativeCompareAgent()
        with self.assertRaises(ValueError):
            agent.compare("", "Valid creative")


if __name__ == "__main__":
    unittest.main()
