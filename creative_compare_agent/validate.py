"""CLI for the Master Creative Validation Agent.

Usage::

    python -m creative_compare_agent.validate \\
        --ptr samples/ptr_email.html \\
        --test samples/test_email.html \\
        --format markdown \\
        [--output report.md]

Accepts plain-text (.txt) or HTML (.html) inputs for each creative.
Text-only inputs run just the text validator; HTML inputs run all three
(text + layout + brand). Image inputs (.png/.jpg) use OCR only if
pytesseract + PIL are importable, otherwise degrade gracefully.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .validators.master import MasterValidationAgent, load_creative, OCR_AVAILABLE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m creative_compare_agent.validate",
        description=(
            "Master Creative Validation Agent — compare a PTR (master) "
            "creative against a Test creative across text accuracy, layout "
            "& alignment, and brand compliance."
        ),
    )
    parser.add_argument("--ptr", required=True, help="Path to the PTR (master) creative (.txt/.html/.png/.jpg).")
    parser.add_argument("--test", required=True, help="Path to the Test creative (.txt/.html/.png/.jpg).")
    parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format (default: markdown).",
    )
    parser.add_argument(
        "--output",
        help="Write the report to this file instead of stdout.",
    )
    parser.add_argument(
        "--ptr-label",
        help="Optional label for the PTR creative (defaults to filename).",
    )
    parser.add_argument(
        "--test-label",
        help="Optional label for the Test creative (defaults to filename).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        ptr = load_creative(args.ptr, label=args.ptr_label)
        test = load_creative(args.test, label=args.test_label)
    except FileNotFoundError as exc:
        parser.error(f"Input file not found: {exc.filename}")
        return 2

    agent = MasterValidationAgent(ptr, test)
    report = agent.validate()

    if args.format == "json":
        rendered = report.to_json()
    else:
        rendered = report.to_markdown()

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        # Concise status to stderr so stdout stays clean when piped.
        print(
            f"Wrote {args.format} report to {args.output} "
            f"(score {report.score()}/100, {report.total_variances} variances).",
            file=sys.stderr,
        )
    else:
        print(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
