"""Tests for the Flask web UI (webapp/app.py).

Uses Flask's test_client — no real socket is bound.
"""

import os
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLES = os.path.join(_PROJECT_ROOT, "samples")


def _read(name):
    with open(os.path.join(_SAMPLES, name), "r", encoding="utf-8") as fh:
        return fh.read()


class WebAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from webapp.app import app

        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_index_get_returns_form(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("<form", body)
        self.assertIn('name="ptr_text"', body)
        self.assertIn('name="test_text"', body)

    def test_index_sample_prefill(self):
        resp = self.client.get("/?sample=1")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        # A distinctive token from the sample HTML should appear in the textarea.
        self.assertIn("<textarea", body)

    def test_post_samples_produces_report(self):
        ptr = _read("ptr_email.html")
        test = _read("test_email.html")
        resp = self.client.post(
            "/validate",
            data={"ptr_text": ptr, "test_text": test},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("Master Creative Validation Report", body)
        # Verdict rendered.
        self.assertTrue("Verdict" in body)
        # At least one variance row rendered in the itemized table.
        self.assertIn('class="variances"', body)
        self.assertIn('class="badge', body)

    def test_post_empty_is_graceful(self):
        resp = self.client.post(
            "/validate",
            data={"ptr_text": "", "test_text": ""},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.get_data(as_text=True)
        self.assertIn("provide BOTH", body)
        # No stack trace leaked.
        self.assertNotIn("Traceback", body)

    def test_download_links_work(self):
        ptr = _read("ptr_email.html")
        test = _read("test_email.html")
        resp = self.client.post(
            "/validate",
            data={"ptr_text": ptr, "test_text": test},
            content_type="multipart/form-data",
        )
        body = resp.get_data(as_text=True)
        # Extract the token from a download link.
        import re

        m = re.search(r"/download/([0-9a-f]+)\.md", body)
        self.assertIsNotNone(m, "expected a markdown download link")
        token = m.group(1)

        md = self.client.get(f"/download/{token}.md")
        self.assertEqual(md.status_code, 200)
        self.assertIn("# Master Creative Validation Report", md.get_data(as_text=True))

        js = self.client.get(f"/download/{token}.json")
        self.assertEqual(js.status_code, 200)
        self.assertIn('"verdict"', js.get_data(as_text=True))

    def test_file_upload_precedence(self):
        import io

        ptr = _read("ptr_email.html")
        test = _read("test_email.html")
        resp = self.client.post(
            "/validate",
            data={
                "ptr_text": "ignored because file wins",
                "ptr_file": (io.BytesIO(ptr.encode("utf-8")), "ptr_email.html"),
                "test_text": test,
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Master Creative Validation Report", resp.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
