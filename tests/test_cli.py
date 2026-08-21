import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CreativeCompareCliTests(unittest.TestCase):
    def test_cli_json_from_sample_file(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "creative_compare_agent.cli",
                "--input-json",
                str(ROOT / "samples" / "comparison.json"),
                "--format",
                "json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["name_a"], "Convenience Concept")
        self.assertIn("recommendation", parsed)

    def test_cli_writes_markdown_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "scorecard.md"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "creative_compare_agent.cli",
                    "--a",
                    "Try FreshBox today for easy family dinners.",
                    "--b",
                    "Win back weeknights with healthy 15-minute dinners for busy parents.",
                    "--audience",
                    "busy parents",
                    "--format",
                    "markdown",
                    "--output",
                    str(out),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Creative Compare Scorecard", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
