"""Text accuracy validation using :mod:`difflib`.

Compares the PTR (master) text against the Test text and reports:

* Exact text mismatches (wrong words / typos / replaced words)
* Missing words (present in PTR, absent in Test)
* Extra words (present in Test, absent in PTR)
* Whitespace / spacing differences
* Line-break differences

Each finding becomes a :class:`Variance` with the PTR value, Test value,
and a plain-English description.

Performance
-----------
A naive ``SequenceMatcher.get_opcodes()`` over the *entire* word-token
list of two creatives is near-quadratic. On a realistic ~400KB marketing
email this takes 60-85s and gets killed by Render/gunicorn's 30s limit,
surfacing as a bare 500 error.

To keep the diff near-linear we do a two-level diff:

1. **Coarse pass** – split both texts into small *segments* (sentence /
   line boundaries, capped in size) and run ``SequenceMatcher`` over the
   segment strings. Equal segments are skipped for free.
2. **Fine pass** – only for the small changed regions do we run the
   original per-word ``SequenceMatcher``, mapping word indices back to
   their global positions so the reported ``word N`` locations and
   messages stay correct.

Both passes are bounded: we never run ``get_opcodes`` on a word list
longer than ``_REGION_WORD_CAP`` tokens; a bigger changed region is
reported as a single block-level replace variance instead of per-word.
Small inputs take the original single-pass path unchanged, so existing
behaviour (and the test suite) is preserved exactly.
"""

from __future__ import annotations

import difflib
import re
from typing import List, Tuple

from .models import Variance


_WORD_RE = re.compile(r"\S+")

# Inputs at or below this many word-tokens use the original single-pass
# diff (fast enough, and output is byte-for-byte identical to before).
_SMALL_WORD_LIMIT = 2000

# Never run per-word get_opcodes on a changed region larger than this;
# bigger regions are summarised as a single block-level replace variance.
_REGION_WORD_CAP = 2000

# Above this total word count we skip fine-grained diffing entirely and
# emit a single capped summary variance — a hard safety net against
# pathological multi-megabyte inputs.
_HUGE_WORD_LIMIT = 120000

# Coarse segment size cap (in words). Sentence punctuation also ends a
# segment; this bounds runaway lines with no punctuation.
_SEGMENT_MAX_WORDS = 60

# Sentence/segment terminating characters.
_SENT_END = ".!?:;"


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
    # Word-level (two-level, near-linear)
    # ------------------------------------------------------------------

    def _word_level(self, ptr_text: str, test_text: str) -> List[Variance]:
        ptr_words = _tokenize_words(ptr_text)
        test_words = _tokenize_words(test_text)

        # Fast path: small inputs use the original single-pass diff. Output
        # is identical to the historical implementation (base offsets = 0).
        if (
            len(ptr_words) <= _SMALL_WORD_LIMIT
            and len(test_words) <= _SMALL_WORD_LIMIT
        ):
            return self._emit_word_opcodes(ptr_words, test_words, 0, 0)

        # Hard safety net for pathological inputs: don't even attempt a
        # fine diff — report a single capped block-level variance.
        if (
            len(ptr_words) > _HUGE_WORD_LIMIT
            or len(test_words) > _HUGE_WORD_LIMIT
        ):
            if ptr_words == test_words:
                return []
            return [self._block_replace(ptr_words, test_words, 0, 0, huge=True)]

        # Large inputs: coarse segment diff, then per-word only on the
        # small changed regions.
        return self._word_level_chunked(ptr_text, test_text, ptr_words, test_words)

    def _word_level_chunked(
        self,
        ptr_text: str,
        test_text: str,
        ptr_words: List[str],
        test_words: List[str],
    ) -> List[Variance]:
        variances: List[Variance] = []

        p_segs = self._build_segments(ptr_words)
        t_segs = self._build_segments(test_words)
        p_keys = [" ".join(ptr_words[s:e]) for (s, e) in p_segs]
        t_keys = [" ".join(test_words[s:e]) for (s, e) in t_segs]

        # The coarse pass runs over segment *strings*. Marketing emails are
        # full of repeated boilerplate segments, which is the exact
        # "popular duplicate" scenario difflib's autojunk heuristic handles;
        # enabling it here keeps the coarse pass near-linear (hundreds of x
        # faster) without affecting the result — changed regions are still
        # re-diffed word-by-word below with autojunk=False.
        matcher = difflib.SequenceMatcher(a=p_keys, b=t_keys, autojunk=True)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue

            # Map coarse segment ranges back to global word ranges.
            p_start = p_segs[i1][0] if i1 < len(p_segs) else len(ptr_words)
            p_end = p_segs[i2 - 1][1] if i2 > i1 else p_start
            t_start = t_segs[j1][0] if j1 < len(t_segs) else len(test_words)
            t_end = t_segs[j2 - 1][1] if j2 > j1 else t_start

            region_ptr = ptr_words[p_start:p_end]
            region_test = test_words[t_start:t_end]

            if tag == "replace":
                if max(len(region_ptr), len(region_test)) > _REGION_WORD_CAP:
                    variances.append(
                        self._block_replace(
                            region_ptr, region_test, p_start, t_start
                        )
                    )
                else:
                    variances.extend(
                        self._emit_word_opcodes(
                            region_ptr, region_test, p_start, t_start
                        )
                    )
            elif tag == "delete":
                variances.extend(
                    self._emit_word_opcodes(region_ptr, [], p_start, t_start)
                )
            elif tag == "insert":
                variances.extend(
                    self._emit_word_opcodes([], region_test, p_start, t_start)
                )
        return variances

    @staticmethod
    def _build_segments(words: List[str]) -> List[Tuple[int, int]]:
        """Partition ``words`` into small (start, end) segments.

        A segment ends at sentence-terminating punctuation or after
        ``_SEGMENT_MAX_WORDS`` tokens, whichever comes first. These
        segments are the units of the coarse diff pass; keeping them small
        and sentence-aligned means the coarse diff finds stable anchors and
        the changed regions handed to the fine pass stay tiny.
        """
        segments: List[Tuple[int, int]] = []
        start = 0
        n = len(words)
        for k in range(n):
            w = words[k]
            if w[-1:] in _SENT_END or (k - start + 1) >= _SEGMENT_MAX_WORDS:
                segments.append((start, k + 1))
                start = k + 1
        if start < n:
            segments.append((start, n))
        return segments

    def _emit_word_opcodes(
        self,
        ptr_words: List[str],
        test_words: List[str],
        base_i: int,
        base_j: int,
    ) -> List[Variance]:
        """Run the original per-word diff over a (bounded) region.

        ``base_i`` / ``base_j`` are the global word offsets of this region
        in the PTR / Test streams; they are added to every reported index
        so ``word N`` locations and descriptions match the whole-document
        numbering. With base offsets of 0 and the full word lists this is
        byte-for-byte identical to the historical single-pass diff.
        """
        variances: List[Variance] = []
        matcher = difflib.SequenceMatcher(a=ptr_words, b=test_words, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            ptr_seg = " ".join(ptr_words[i1:i2])
            test_seg = " ".join(test_words[j1:j2])

            gi1, gi2 = base_i + i1, base_i + i2

            if tag == "replace":
                severity = self._replace_severity(ptr_seg, test_seg)
                category = "typo" if self._looks_like_typo(ptr_seg, test_seg) else "wording"
                desc = (
                    f"Text changed at word {gi1 + 1}: "
                    f"PTR says \"{_truncate(ptr_seg)}\" but Test says "
                    f"\"{_truncate(test_seg)}\"."
                )
                variances.append(
                    Variance(
                        dimension="text",
                        severity=severity,
                        location=f"word {gi1 + 1}-{gi2}",
                        ptr_value=ptr_seg,
                        test_value=test_seg,
                        description=desc,
                        category=category,
                    )
                )
            elif tag == "delete":
                desc = (
                    f"Missing text: PTR contains \"{_truncate(ptr_seg)}\" "
                    f"(word {gi1 + 1}-{gi2}) which is absent from Test."
                )
                variances.append(
                    Variance(
                        dimension="text",
                        severity="major",
                        location=f"word {gi1 + 1}-{gi2}",
                        ptr_value=ptr_seg,
                        test_value="",
                        description=desc,
                        category="missing_words",
                    )
                )
            elif tag == "insert":
                desc = (
                    f"Extra text: Test adds \"{_truncate(test_seg)}\" "
                    f"(after PTR word {gi1}) not present in PTR."
                )
                variances.append(
                    Variance(
                        dimension="text",
                        severity="major",
                        location=f"after word {gi1}",
                        ptr_value="",
                        test_value=test_seg,
                        description=desc,
                        category="extra_words",
                    )
                )
        return variances

    def _block_replace(
        self,
        region_ptr: List[str],
        region_test: List[str],
        base_i: int,
        base_j: int,
        huge: bool = False,
    ) -> Variance:
        """Summarise an oversized changed region as one variance.

        Used when a changed region is too large to diff word-by-word
        within the request budget. Values are truncated.
        """
        ptr_seg = " ".join(region_ptr)
        test_seg = " ".join(region_test)
        gi1 = base_i + 1
        gi2 = base_i + len(region_ptr)
        where = "The text differs substantially" if huge else (
            f"Large text block changed near word {gi1}"
        )
        desc = (
            f"{where}: PTR says \"{_truncate(ptr_seg)}\" but Test says "
            f"\"{_truncate(test_seg)}\". (Region too large to diff word-by-word.)"
        )
        return Variance(
            dimension="text",
            severity="major",
            location=(f"word {gi1}-{gi2}" if not huge else "document"),
            ptr_value=_truncate(ptr_seg),
            test_value=_truncate(test_seg),
            description=desc,
            category="wording",
        )

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
