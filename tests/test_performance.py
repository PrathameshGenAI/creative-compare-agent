"""Performance & robustness tests for the text-diff hot path.

These guard against the O(n^2) regression that caused 30s+ requests
(and gunicorn 500s) on realistic marketing emails, and confirm the
input-size safety net keeps huge inputs from blowing up.
"""

import time
import unittest

from creative_compare_agent.validators.master import (
    MasterValidationAgent,
    creative_from_string,
)


def _build_email(variant=False, paragraphs=1400):
    """Build a realistic ~300-400KB HTML marketing email.

    When ``variant`` is True we inject three known differences:
      * a typo ("single-orgin"),
      * a changed CTA ("Shop the Sale" -> "Buy Now"),
      * a missing footer (the unsubscribe line is dropped).
    """
    blocks = [
        "<html><body>",
        "<h1 style='text-align:center;color:#ff6600'>Summer Sale Spectacular</h1>",
    ]
    for i in range(paragraphs):
        blocks.append(
            f"<p>Discover amazing deals on our single-origin coffee collection "
            f"number {i}. Enjoy free shipping on orders over fifty dollars and "
            f"exclusive member rewards. Our roasters carefully select every bean "
            f"for a rich and balanced flavor profile every single day.</p>"
        )
    if not variant:
        blocks.append("<p>Our single-origin beans are the best in town.</p>")
        blocks.append("<a href='#' style='color:#ff6600'>Shop the Sale</a>")
        blocks.append(
            "<footer>Unsubscribe anytime here at the bottom footer section.</footer>"
        )
    else:
        blocks.append("<p>Our single-orgin beans are the best in town.</p>")  # typo
        blocks.append("<a href='#' style='color:#0000ff'>Buy Now</a>")  # changed CTA
        # footer intentionally missing
    blocks.append("</body></html>")
    return "\n".join(blocks)


class PerformanceTests(unittest.TestCase):
    def test_large_email_is_fast_and_accurate(self):
        ptr_html = _build_email(variant=False)
        test_html = _build_email(variant=True)

        # Sanity: this really is a large (~300-400KB) creative.
        self.assertGreater(len(ptr_html), 300_000)

        ptr = creative_from_string(ptr_html, label="PTR")
        test = creative_from_string(test_html, label="Test")

        start = time.perf_counter()
        report = MasterValidationAgent(ptr, test).validate()
        elapsed = time.perf_counter() - start

        # Must be well under Render's 30s ceiling — we require < 5s.
        self.assertLess(
            elapsed,
            5.0,
            msg=f"validate() took {elapsed:.2f}s on a large email (expected < 5s)",
        )

        # And it must still surface the three planted differences.
        text_blob = " ".join(
            f"{v.location} {v.ptr_value} {v.test_value} {v.description}"
            for v in report.variances
            if v.dimension == "text"
        ).lower()

        self.assertIn("single-orgin", text_blob, "typo not detected")
        self.assertIn("buy now", text_blob, "changed CTA not detected")
        self.assertIn(
            "unsubscribe", text_blob, "missing footer text not detected"
        )

    def test_huge_input_is_handled_gracefully(self):
        # > 2,000,000 characters on each side.
        huge = ("word " * 500_000).strip()  # ~2.5M chars
        other = ("word " * 500_000 + "different ").strip()
        self.assertGreater(len(huge), 2_000_000)

        ptr = creative_from_string(huge, label="PTR")
        test = creative_from_string(other, label="Test")

        start = time.perf_counter()
        # Must not raise, and must not hang.
        report = MasterValidationAgent(ptr, test).validate()
        elapsed = time.perf_counter() - start

        self.assertLess(
            elapsed,
            10.0,
            msg=f"huge input took {elapsed:.2f}s (expected graceful/fast handling)",
        )
        # A valid report object comes back either way.
        self.assertIsNotNone(report)
        self.assertIsInstance(report.total_variances, int)


class WebAppLargeInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from webapp.app import app

        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_large_html_upload_returns_200(self):
        import io

        ptr_html = _build_email(variant=False)
        test_html = _build_email(variant=True)

        data = {
            "ptr_file": (io.BytesIO(ptr_html.encode("utf-8")), "ptr.html"),
            "test_file": (io.BytesIO(test_html.encode("utf-8")), "test.html"),
        }
        resp = self.client.post(
            "/validate", data=data, content_type="multipart/form-data"
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # A rendered report, not a bare error.
        self.assertNotIn("Internal Server Error", body)

    def test_oversized_input_gets_friendly_message(self):
        # Over the 2,000,000-char per-creative cap -> friendly page, HTTP 200.
        big = "x" * 2_100_000
        resp = self.client.post(
            "/validate",
            data={"ptr_text": big, "test_text": "hello world"},
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True).lower()
        self.assertIn("too large", body)


if __name__ == "__main__":
    unittest.main()
