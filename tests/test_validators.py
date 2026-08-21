"""Tests for the Master Creative Validation Agent and its sub-validators.

Covers each validator individually plus the master orchestrator on the
sample PTR/Test HTML pair, asserting the intentionally injected variances
are all detected.
"""

import json
import os
import unittest

from creative_compare_agent.validators.text_validator import TextAccuracyValidator
from creative_compare_agent.validators.layout_validator import LayoutAlignmentInspector
from creative_compare_agent.validators.brand_validator import BrandComplianceAuditor
from creative_compare_agent.validators.master import (
    MasterValidationAgent,
    validate_files,
    load_creative,
)
from creative_compare_agent.validators.models import ValidationReport, Variance


SAMPLES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples")
PTR_HTML = os.path.join(SAMPLES, "ptr_email.html")
TEST_HTML = os.path.join(SAMPLES, "test_email.html")


def _joined(variances):
    return " ".join(
        f"{v.location} {v.ptr_value} {v.test_value} {v.description}".lower()
        for v in variances
    )


class TextAccuracyValidatorTests(unittest.TestCase):
    def test_detects_word_change(self):
        v = TextAccuracyValidator().validate(
            "Shop the Sale today", "Buy Now today"
        )
        self.assertTrue(v, "expected at least one variance")
        self.assertTrue(all(x.dimension == "text" for x in v))

    def test_detects_typo(self):
        v = TextAccuracyValidator().validate("single-origin", "single-orgin")
        self.assertIn("single-orgin", _joined(v))

    def test_detects_missing_words(self):
        v = TextAccuracyValidator().validate(
            "Unsubscribe anytime here", "here"
        )
        self.assertTrue(any("unsubscribe" in x.ptr_value.lower() for x in v))

    def test_identical_text_no_variance(self):
        v = TextAccuracyValidator().validate("same text here", "same text here")
        self.assertEqual(v, [])


class LayoutAlignmentInspectorTests(unittest.TestCase):
    def setUp(self):
        with open(PTR_HTML, encoding="utf-8") as fh:
            self.ptr = fh.read()
        with open(TEST_HTML, encoding="utf-8") as fh:
            self.test = fh.read()

    def test_detects_alignment_shift(self):
        v = LayoutAlignmentInspector().validate(self.ptr, self.test)
        joined = _joined(v)
        self.assertIn("center", joined)
        self.assertIn("left", joined)
        self.assertTrue(all(x.dimension == "layout" for x in v))

    def test_detects_missing_or_extra_element(self):
        v = LayoutAlignmentInspector().validate(self.ptr, self.test)
        joined = _joined(v)
        self.assertTrue("missing" in joined or "extra" in joined)


class BrandComplianceAuditorTests(unittest.TestCase):
    def setUp(self):
        with open(PTR_HTML, encoding="utf-8") as fh:
            self.ptr = fh.read()
        with open(TEST_HTML, encoding="utf-8") as fh:
            self.test = fh.read()

    def test_detects_color_change(self):
        v = BrandComplianceAuditor().validate(self.ptr, self.test)
        joined = _joined(v)
        self.assertIn("#ff6600", joined)
        self.assertTrue(all(x.dimension == "brand" for x in v))

    def test_detects_cta_text_change(self):
        v = BrandComplianceAuditor().validate(self.ptr, self.test)
        joined = _joined(v)
        self.assertIn("buy now", joined)


class MasterOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.report = validate_files(PTR_HTML, TEST_HTML)

    def test_runs_all_three_dimensions(self):
        self.assertEqual(
            set(self.report.dimensions_run), {"text", "layout", "brand"}
        )

    def test_all_injected_variances_detected(self):
        joined = _joined(self.report.variances)
        # typo
        self.assertIn("single-orgin", joined)
        # alignment change center -> left
        self.assertIn("center", joined)
        self.assertIn("left", joined)
        # CTA / color change
        self.assertIn("#ff6600", joined)
        # CTA text change
        self.assertIn("buy now", joined)
        # a missing element (footer/unsubscribe)
        self.assertTrue(
            "missing" in joined and "unsubscribe" in joined,
            "expected missing footer/unsubscribe element",
        )

    def test_report_has_variances_across_dimensions(self):
        counts = self.report.counts_by_dimension()
        self.assertGreater(counts["text"], 0)
        self.assertGreater(counts["layout"], 0)
        self.assertGreater(counts["brand"], 0)

    def test_score_and_verdict(self):
        self.assertLess(self.report.score(), 100.0)
        self.assertIsInstance(self.report.verdict(), str)

    def test_markdown_and_json_serializable(self):
        md = self.report.to_markdown()
        self.assertIn("Master Creative Validation Report", md)
        data = json.loads(self.report.to_json())
        self.assertIn("variances", data)
        self.assertIn("summary", data)

    def test_string_inputs_text_only(self):
        report = MasterValidationAgent(
            "Hello world", "Hello there"
        ).validate()
        self.assertEqual(report.dimensions_run, ["text"])
        self.assertTrue(report.variances)

    def test_identical_html_is_clean(self):
        with open(PTR_HTML, encoding="utf-8") as fh:
            src = fh.read()
        report = MasterValidationAgent(
            load_creative(PTR_HTML), load_creative(PTR_HTML)
        ).validate()
        self.assertEqual(report.total_variances, 0)
        self.assertEqual(report.verdict(), "PASS — exact match")


if __name__ == "__main__":
    unittest.main()
