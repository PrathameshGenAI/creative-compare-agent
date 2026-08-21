"""Text accuracy validation using :mod:`difflib`.

Compares the PTR (master) text against the Test text and reports:

* Exact text mismatches (wrong words / typos / replaced words)
* Missing words (present in PTR, absent in Test)
* Extra words (present in Test, absent in PTR)
* Whitespace / spacing differences
* Line-break differences

Each finding becomes a :class:`Variance` with the PTR value, Test value,
and a plain-English description.
"""

from __future__ import annotations

import difflib
import re
from typing import List

from .models import Variance


_WORD_RE = re.compile(r"\S+")


def _tokenize_words(text: str) -> List[str]:
    """Split text into whitespace-delimited tokens (keeps punctuation)."""
    return _WORD_RE.findall(text or "")


def _norm_spaces(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text or "")


def _truncate(value: str, limit: int = 120) -> str:
    value = value.replace("\n", "\\n")
    if len(value) > limit:
        return value[: limit - 1] + "…"
    return value


class TextAccuracyValidator:
    """Validate that Test text matches PTR text exactly.

    Usage::

        variances = TextAccuracyValidator().validate(ptr_text, test_text)
    """

    dimension = "text"

    def validate(self, ptr_text: str, test_text: str) -> List[Variance]:
        variances: List[Variance] = []

        ptr_text = ptr_text or ""
        test_text = test_text or ""

        # 1) Word-level diff (captures typos, wrong/missing/extra words).
        variances.extend(self._word_level(ptr_text, test_text))

        # 2) Whitespace / spacing differences (multiple vs single spaces,
        #    trailing spaces) — only if the collapsed text otherwise matches
        #    on a line, so we don't double-report word substitutions.
        variances.extend(self._spacing(ptr_text, test_text))

        # 3) Line-break differences (line count / blank lines).
        variances.extend(self._line_breaks(ptr_text, test_text))

        return variances

    # ------------------------------------------------------------------
    # Word-level
    # ------------------------------------------------------------------

    def _word_level(self, ptr_text: str, test_text: str) -> List[Variance]:
        variances: List[Variance] = []
        ptr_words = _tokenize_words(ptr_text)
        test_words = _tokenize_words(test_text)

        matcher = difflib.SequenceMatcher(a=ptr_words, b=test_words, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            ptr_seg = " ".join(ptr_words[i1:i2])
            test_seg = " ".join(test_words[j1:j2])

            if tag == "replace":
                severity = self._replace_severity(ptr_seg, test_seg)
                category = "typo" if self._looks_like_typo(ptr_seg, test_seg) else "wording"
                desc = (
                    f"Text changed at word {i1 + 1}: "
                    f"PTR says \"{_truncate(ptr_seg)}\" but Test says "
                    f"\"{_truncate(test_seg)}\"."
                )
                variances.append(
                    Variance(
                        dimension="text",
                        severity=severity,
                        location=f"word {i1 + 1}-{i2}",
                        ptr_value=ptr_seg,
                        test_value=test_seg,
                        description=desc,
                        category=category,
                    )
                )
            elif tag == "delete":
                desc = (
                    f"Missing text: PTR contains \"{_truncate(ptr_seg)}\" "
                    f"(word {i1 + 1}-{i2}) which is absent from Test."
                )
                variances.append(
                    Variance(
                        dimension="text",
                        severity="major",
                        location=f"word {i1 + 1}-{i2}",
                        ptr_value=ptr_seg,
                        test_value="",
                        description=desc,
                        category="missing_words",
                    )
                )
            elif tag == "insert":
                desc = (
                    f"Extra text: Test adds \"{_truncate(test_seg)}\" "
                    f"(after PTR word {i1}) not present in PTR."
                )
                variances.append(
                    Variance(
                        dimension="text",
                        severity="major",
                        location=f"after word {i1}",
                        ptr_value="",
                        test_value=test_seg,
                        description=desc,
                        category="extra_words",
                    )
                )
        return variances

    def _replace_severity(self, ptr_seg: str, test_seg: str) -> str:
        # A single-word substitution that looks like a typo is minor-to-major;
        # larger rewrites are more serious.
        ptr_words = ptr_seg.split()
        test_words = test_seg.split()
        if len(ptr_words) <= 1 and len(test_words) <= 1:
            return "major" if not self._looks_like_typo(ptr_seg, test_seg) else "minor"
        return "major"

    @staticmethod
    def _looks_like_typo(ptr_seg: str, test_seg: str) -> bool:
        """Heuristic: single word, small character-level edit distance."""
        a = ptr_seg.strip()
        b = test_seg.strip()
        if not a or not b:
            return False
        if " " in a or " " in b:
            return False
        ratio = difflib.SequenceMatcher(a=a.lower(), b=b.lower()).ratio()
        return ratio >= 0.6

    # ------------------------------------------------------------------
    # Spacing
    # ------------------------------------------------------------------

    def _spacing(self, ptr_text: str, test_text: str) -> List[Variance]:
        variances: List[Variance] = []

        # Compare line-by-line after collapsing internal runs of spaces.
        ptr_lines = ptr_text.split("\n")
        test_lines = test_text.split("\n")

        for idx, (p_line, t_line) in enumerate(zip(ptr_lines, test_lines), start=1):
            if p_line == t_line:
                continue
            # If the only difference is whitespace runs, report a spacing issue.
            if _norm_spaces(p_line).strip() == _norm_spaces(t_line).strip() and \
                    p_line.strip() == t_line.strip():
                # differs only by internal/leading/trailing whitespace amount
                variances.append(
                    Variance(
                        dimension="text",
                        severity="minor",
                        location=f"line {idx}",
                        ptr_value=_truncate(repr(p_line)),
                        test_value=_truncate(repr(t_line)),
                        description=(
                            f"Spacing/whitespace differs on line {idx} "
                            "(same words, different spacing)."
                        ),
                        category="spacing",
                    )
                )
        return variances

    # ------------------------------------------------------------------
    # Line breaks
    # ------------------------------------------------------------------

    def _line_breaks(self, ptr_text: str, test_text: str) -> List[Variance]:
        variances: List[Variance] = []
        ptr_lines = ptr_text.split("\n")
        test_lines = test_text.split("\n")
        if len(ptr_lines) != len(test_lines):
            variances.append(
                Variance(
                    dimension="text",
                    severity="minor",
                    location="line structure",
                    ptr_value=f"{len(ptr_lines)} lines",
                    test_value=f"{len(test_lines)} lines",
                    description=(
                        f"Line-break structure differs: PTR has "
                        f"{len(ptr_lines)} line(s) but Test has "
                        f"{len(test_lines)} line(s)."
                    ),
                    category="line_break",
                )
            )
        return variances
