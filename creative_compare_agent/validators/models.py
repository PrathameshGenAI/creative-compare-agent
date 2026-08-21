"""Shared data models for the Master Creative Validation Agent.

A ``Variance`` is a single detected difference between the PTR (master)
creative and the Test creative. A ``ValidationReport`` is the unified
collection of variances produced by the master orchestrator, with
summary counts, a pass score, and Markdown/JSON renderers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

DIMENSIONS = ("text", "layout", "brand")

# Ordered from most to least serious. Used for sorting and scoring.
SEVERITIES = ("critical", "major", "minor")

# Score weight removed from a perfect 100 for each variance, by severity.
_SEVERITY_PENALTY = {
    "critical": 12.0,
    "major": 6.0,
    "minor": 2.0,
}

_SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}
_DIMENSION_RANK = {name: rank for rank, name in enumerate(DIMENSIONS)}


# ---------------------------------------------------------------------------
# Variance
# ---------------------------------------------------------------------------


@dataclass
class Variance:
    """A single detected difference between PTR and Test creatives.

    Attributes:
        dimension: One of ``DIMENSIONS`` (text / layout / brand).
        severity: One of ``SEVERITIES`` (critical / major / minor).
        location: Human-readable element or location descriptor.
        ptr_value: The value found in the PTR (master) creative.
        test_value: The value found in the Test creative.
        description: Plain-English explanation of the variance.
        category: Optional finer-grained category (e.g. "typo",
            "alignment", "missing_element", "font", "color", "cta").
    """

    dimension: str
    severity: str
    location: str
    ptr_value: str
    test_value: str
    description: str
    category: str = ""

    def __post_init__(self) -> None:
        if self.dimension not in DIMENSIONS:
            raise ValueError(
                f"Unknown dimension {self.dimension!r}; expected one of {DIMENSIONS}"
            )
        if self.severity not in SEVERITIES:
            raise ValueError(
                f"Unknown severity {self.severity!r}; expected one of {SEVERITIES}"
            )

    @property
    def sort_key(self) -> tuple:
        return (
            _DIMENSION_RANK.get(self.dimension, 99),
            _SEVERITY_RANK.get(self.severity, 99),
            self.location,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# ValidationReport
# ---------------------------------------------------------------------------


@dataclass
class ValidationReport:
    """Unified variance report produced by the master orchestrator."""

    variances: List[Variance] = field(default_factory=list)
    dimensions_run: List[str] = field(default_factory=list)
    ptr_label: str = "PTR"
    test_label: str = "Test"
    notes: List[str] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    # -- aggregation --------------------------------------------------------

    def add(self, variance: Variance) -> None:
        self.variances.append(variance)

    def extend(self, variances: List[Variance]) -> None:
        self.variances.extend(variances)

    @property
    def sorted_variances(self) -> List[Variance]:
        return sorted(self.variances, key=lambda v: v.sort_key)

    def counts_by_dimension(self) -> Dict[str, int]:
        counts = {dim: 0 for dim in DIMENSIONS}
        for v in self.variances:
            counts[v.dimension] = counts.get(v.dimension, 0) + 1
        return counts

    def counts_by_severity(self) -> Dict[str, int]:
        counts = {sev: 0 for sev in SEVERITIES}
        for v in self.variances:
            counts[v.severity] = counts.get(v.severity, 0) + 1
        return counts

    @property
    def total_variances(self) -> int:
        return len(self.variances)

    def score(self) -> float:
        """Match/pass score from 0-100.

        Starts at 100 and subtracts a severity-weighted penalty per
        variance, floored at 0. A perfect match scores 100.
        """
        penalty = sum(_SEVERITY_PENALTY.get(v.severity, 0.0) for v in self.variances)
        return round(max(0.0, 100.0 - penalty), 1)

    @property
    def passed(self) -> bool:
        """True when there are no critical variances."""
        return self.counts_by_severity().get("critical", 0) == 0

    def verdict(self) -> str:
        if self.total_variances == 0:
            return "PASS — exact match"
        if not self.passed:
            return "FAIL — critical variances present"
        score = self.score()
        if score >= 90:
            return "PASS WITH MINOR NOTES"
        if score >= 70:
            return "REVIEW — several variances"
        return "FAIL — extensive variances"

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "ptr_label": self.ptr_label,
            "test_label": self.test_label,
            "dimensions_run": list(self.dimensions_run),
            "score": self.score(),
            "passed": self.passed,
            "verdict": self.verdict(),
            "summary": {
                "total_variances": self.total_variances,
                "by_dimension": self.counts_by_dimension(),
                "by_severity": self.counts_by_severity(),
            },
            "variances": [v.to_dict() for v in self.sorted_variances],
            "notes": list(self.notes),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_markdown(self) -> str:
        lines: List[str] = []
        lines.append("# Master Creative Validation Report")
        lines.append("")
        lines.append(f"- **PTR (master):** {self.ptr_label}")
        lines.append(f"- **Test:** {self.test_label}")
        lines.append(f"- **Generated:** {self.generated_at}")
        lines.append(
            f"- **Dimensions run:** {', '.join(self.dimensions_run) or 'none'}"
        )
        lines.append("")
        lines.append(f"## Verdict: {self.verdict()}")
        lines.append("")
        lines.append(f"- **Match score:** {self.score()} / 100")
        lines.append(f"- **Passed (no critical):** {'yes' if self.passed else 'no'}")
        lines.append(f"- **Total variances:** {self.total_variances}")
        lines.append("")

        # Summary tables
        lines.append("### Variances by dimension")
        lines.append("")
        lines.append("| Dimension | Count |")
        lines.append("| --- | ---: |")
        for dim, count in self.counts_by_dimension().items():
            lines.append(f"| {dim.capitalize()} | {count} |")
        lines.append("")

        lines.append("### Variances by severity")
        lines.append("")
        lines.append("| Severity | Count |")
        lines.append("| --- | ---: |")
        for sev, count in self.counts_by_severity().items():
            lines.append(f"| {sev.capitalize()} | {count} |")
        lines.append("")

        # Itemized list
        lines.append("## Itemized variances")
        lines.append("")
        if not self.variances:
            lines.append("_No variances detected — creatives match._")
        else:
            lines.append(
                "| # | Dimension | Severity | Location | PTR value | Test value | Description |"
            )
            lines.append("| ---: | --- | --- | --- | --- | --- | --- |")
            for i, v in enumerate(self.sorted_variances, start=1):
                lines.append(
                    "| {n} | {dim} | {sev} | {loc} | {ptr} | {test} | {desc} |".format(
                        n=i,
                        dim=v.dimension,
                        sev=v.severity,
                        loc=_md_cell(v.location),
                        ptr=_md_cell(v.ptr_value),
                        test=_md_cell(v.test_value),
                        desc=_md_cell(v.description),
                    )
                )
        lines.append("")

        if self.notes:
            lines.append("## Notes")
            lines.append("")
            for note in self.notes:
                lines.append(f"- {note}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"


def _md_cell(value: Optional[str]) -> str:
    """Escape a value for safe rendering inside a Markdown table cell."""
    if value is None:
        return "—"
    text = str(value)
    if text == "":
        return "—"
    text = text.replace("\\", "\\\\")
    text = text.replace("|", "\\|")
    text = text.replace("\n", "<br>")
    return text
