"""Tests for PDF upload handling in the web app.

Marketing creatives are frequently exported as PDF. Reading raw PDF bytes as
text produces megabytes of binary garbage (the original 'input too large'
bug), so uploaded PDFs must be parsed and their text layer extracted.
"""
import io
import os
import unittest

from webapp.app import app, _looks_like_pdf, _extract_pdf_text

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
PTR_PDF = os.path.join(FIXTURES, "sample_ptr.pdf")
TEST_PDF = os.path.join(FIXTURES, "sample_test.pdf")


class PdfUploadTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_magic_header_detected(self):
        self.assertTrue(_looks_like_pdf("x.pdf", b""))
        self.assertTrue(_looks_like_pdf("noext", b"%PDF-1.7 rest"))
        self.assertFalse(_looks_like_pdf("x.html", b"<html>"))

    def test_extract_pdf_text_pulls_visible_copy(self):
        with open(PTR_PDF, "rb") as fh:
            text = _extract_pdf_text(fh.read())
        # Real content is only a few KB, not megabytes.
        self.assertLess(len(text), 50_000)
        self.assertIn("USAA", text)

    def test_pdf_upload_produces_report_not_too_large(self):
        with open(PTR_PDF, "rb") as f1, open(TEST_PDF, "rb") as f2:
            data = {
                "ptr_file": (io.BytesIO(f1.read()), "sample_ptr.pdf"),
                "test_file": (io.BytesIO(f2.read()), "sample_test.pdf"),
            }
            resp = self.client.post(
                "/validate", data=data, content_type="multipart/form-data"
            )
        html = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("too large", html.lower())
        self.assertIn("Master Creative Validation Report", html)


if __name__ == "__main__":
    unittest.main()
