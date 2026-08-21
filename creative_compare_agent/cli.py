from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .agent import CreativeCompareAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two creative concepts using local deterministic heuristics."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input-json", help="Path to JSON with creative_a and creative_b fields.")
    source.add_argument("--a", help="Creative A text. Use with --b or --b-file.")
    parser.add_argument("--b", help="Creative B text. Use with --a or --a-file.")
    parser.add_argument("--a-file", help="Path to Creative A text file. Can be used instead of --a when --b/--b-file is set.")
    parser.add_argument("--b-file", help="Path to Creative B text file.")
    parser.add_argument("--name-a", default="Creative A", help="Display name for Creative A.")
    parser.add_argument("--name-b", default="Creative B", help="Display name for Creative B.")
    parser.add_argument("--audience", default="", help="Target audience description.")
    parser.add_argument("--objective", default="", help="Campaign objective.")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", help="Output format.")
    parser.add_argument("--output", help="Optional output file path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        payload = _load_payload(args)
        agent = CreativeCompareAgent()
        scorecard = agent.compare(**payload)
        rendered = agent.to_json(scorecard) if args.format == "json" else agent.to_markdown(scorecard)
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except Exception as exc:  # pragma: no cover - exact argparse/runtime text varies
        parser.exit(2, f"creative-compare: error: {exc}\n")


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.input_json:
        data = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
        return {
            "creative_a": _require(data, "creative_a"),
            "creative_b": _require(data, "creative_b"),
            "audience": data.get("audience", args.audience or ""),
            "objective": data.get("objective", args.objective or ""),
            "name_a": data.get("name_a", args.name_a),
            "name_b": data.get("name_b", args.name_b),
        }

    creative_a = args.a if args.a is not None else _read_optional(args.a_file)
    creative_b = args.b if args.b is not None else _read_optional(args.b_file)
    if not creative_a:
        raise ValueError("provide Creative A via --a or --a-file")
    if not creative_b:
        raise ValueError("provide Creative B via --b or --b-file")
    return {
        "creative_a": creative_a,
        "creative_b": creative_b,
        "audience": args.audience,
        "objective": args.objective,
        "name_a": args.name_a,
        "name_b": args.name_b,
    }


def _read_optional(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).read_text(encoding="utf-8")


def _require(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"input JSON must include non-empty '{key}'")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
