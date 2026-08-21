"""Layout & alignment inspection from parsed HTML.

Checks:

* Text alignment (left / center / right / justify) shifts, from inline
  ``text-align`` styles or the deprecated ``align`` attribute.
* Element ordering / block sequence differences.
* Missing elements (present in PTR, absent in Test).
* Extra elements (present in Test, absent in PTR).
* Visual hierarchy — heading level changes (h1..h6).

Elements are matched between documents by a stable *signature* derived
from tag + a normalized snippet of their text, so we can pair "the same"
element across the two creatives even if it moved. Leftover elements are
paired fuzzily so a mere typo does not surface as both a missing and an
extra element (the text validator already reports the word change).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from .htmldoc import Node, ParsedHTML, parse_html
from .models import Variance


# Block-level / structural tags we track for ordering and presence.
STRUCTURAL_TAGS = {
    "header", "footer", "nav", "main", "section", "article", "aside",
    "div", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "table", "tr", "td", "img", "a", "button",
    "span", "hr", "blockquote",
}

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

_ALIGN_PROPS = ("text-align",)


def _norm_text(text: str, limit: int = 40) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip().lower()
    return text[:limit]


def _element_alignment(node: Node) -> Optional[str]:
    """Return the explicit alignment of a node, if any."""
    style = node.style
    for prop in _ALIGN_PROPS:
        if prop in style:
            return style[prop].lower()
    align_attr = node.get("align", "").lower()
    if align_attr:
        return align_attr
    return None


def _signature(node: Node) -> str:
    """A stable identity for pairing an element across documents."""
    text = _norm_text(node.visible_text())
    if node.tag == "img":
        src = node.get("src", "")
        alt = node.get("alt", "")
        return f"img|{alt.lower()[:20]}|{src.lower()[-20:]}"
    return f"{node.tag}|{text}"


class LayoutAlignmentInspector:
    """Inspect layout, alignment, ordering, and hierarchy differences."""

    dimension = "layout"

    def validate(self, ptr_html: str, test_html: str) -> List[Variance]:
        ptr = parse_html(ptr_html)
        test = parse_html(test_html)
        variances: List[Variance] = []

        ptr_nodes = self._structural_nodes(ptr)
        test_nodes = self._structural_nodes(test)

        variances.extend(self._presence(ptr_nodes, test_nodes))
        variances.extend(self._alignment(ptr_nodes, test_nodes))
        variances.extend(self._ordering(ptr_nodes, test_nodes))
        variances.extend(self._hierarchy(ptr, test))

        return variances

    # ------------------------------------------------------------------

    def _structural_nodes(self, doc: ParsedHTML) -> List[Node]:
        return [n for n in doc.iter_nodes() if n.tag in STRUCTURAL_TAGS]

    def _sig_map(self, nodes: List[Node]) -> Dict[str, Node]:
        result: Dict[str, Node] = {}
        for n in nodes:
            sig = _signature(n)
            result.setdefault(sig, n)  # first occurrence is representative
        return result

    # -- presence -------------------------------------------------------

    def _presence(self, ptr_nodes: List[Node], test_nodes: List[Node]) -> List[Variance]:
        """Report elements present in one creative but not the other.

        Elements are first paired by exact signature. Leftover PTR nodes
        are then fuzzily paired with leftover Test nodes of the same tag
        whose text is highly similar (a typo / minor reword should NOT
        surface as both a missing and an extra element — the text
        validator already reports that word-level change). Only genuinely
        unmatched nodes become missing/extra variances.
        """
        variances: List[Variance] = []

        # Pass 1: consume exact-signature matches pairwise.
        test_remaining = list(test_nodes)
        ptr_unmatched: List[Node] = []
        for node in ptr_nodes:
            sig = _signature(node)
            match_idx = next(
                (i for i, t in enumerate(test_remaining) if _signature(t) == sig),
                None,
            )
            if match_idx is None:
                ptr_unmatched.append(node)
            else:
                test_remaining.pop(match_idx)

        # Pass 2: fuzzily pair leftover PTR nodes with leftover Test nodes.
        ptr_still: List[Node] = []
        for node in ptr_unmatched:
            idx = self._best_fuzzy_match(node, test_remaining)
            if idx is None:
                ptr_still.append(node)
            else:
                test_remaining.pop(idx)  # word change handled by text validator

        # Whatever PTR nodes remain are genuinely missing from Test.
        for node in ptr_still:
            label = self._describe(node)
            variances.append(
                Variance(
                    dimension="layout",
                    severity=self._presence_severity(node.tag),
                    location=label,
                    ptr_value="present",
                    test_value="absent",
                    description=(
                        f"Missing element: {label} appears in PTR but is "
                        f"absent from Test."
                    ),
                    category="missing_element",
                )
            )

        # Whatever Test nodes remain are genuinely extra.
        for node in test_remaining:
            label = self._describe(node)
            variances.append(
                Variance(
                    dimension="layout",
                    severity=self._presence_severity(node.tag),
                    location=label,
                    ptr_value="absent",
                    test_value="present",
                    description=(
                        f"Extra element: {label} appears in Test but is "
                        f"not present in PTR."
                    ),
                    category="extra_element",
                )
            )
        return variances

    @staticmethod
    def _best_fuzzy_match(node: Node, candidates: List[Node]) -> Optional[int]:
        """Index of the best same-tag, highly-similar candidate, or None."""
        target = _norm_text(node.visible_text())
        best_idx: Optional[int] = None
        best_ratio = 0.0
        for i, cand in enumerate(candidates):
            if cand.tag != node.tag:
                continue
            ratio = SequenceMatcher(
                None, target, _norm_text(cand.visible_text())
            ).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = i
        # Require strong similarity so we only absorb typos / minor reword.
        if best_idx is not None and best_ratio >= 0.75:
            return best_idx
        return None

    @staticmethod
    def _presence_severity(tag: str) -> str:
        if tag in {"footer", "header", "img", "a", "button"}:
            return "major"
        if tag in HEADING_TAGS:
            return "major"
        return "minor"

    @staticmethod
    def _describe(node: Node) -> str:
        text = _norm_text(node.visible_text(), 30)
        if node.tag == "img":
            alt = node.get("alt", "")
            return f"<img> \"{alt}\"" if alt else "<img>"
        if text:
            return f"<{node.tag}> \"{text}\""
        return f"<{node.tag}>"

    # -- alignment ------------------------------------------------------

    def _alignment(self, ptr_nodes: List[Node], test_nodes: List[Node]) -> List[Variance]:
        variances: List[Variance] = []
        test_map = self._sig_map(test_nodes)
        seen = set()
        for node in ptr_nodes:
            sig = _signature(node)
            if sig in seen:
                continue
            seen.add(sig)
            counterpart = test_map.get(sig)
            if counterpart is None:
                continue  # handled by presence check
            ptr_align = _element_alignment(node)
            test_align = _element_alignment(counterpart)
            # Normalize None -> implicit default 'left' for comparison only
            p = ptr_align or "left"
            t = test_align or "left"
            if p != t:
                variances.append(
                    Variance(
                        dimension="layout",
                        severity="major",
                        location=self._describe(node),
                        ptr_value=ptr_align or "left (default)",
                        test_value=test_align or "left (default)",
                        description=(
                            f"Alignment shift on {self._describe(node)}: "
                            f"PTR is '{p}' but Test is '{t}'."
                        ),
                        category="alignment",
                    )
                )
        return variances

    # -- ordering -------------------------------------------------------

    def _ordering(self, ptr_nodes: List[Node], test_nodes: List[Node]) -> List[Variance]:
        # Compare the ordered sequence of signatures common to both docs.
        ptr_seq = [_signature(n) for n in ptr_nodes]
        test_seq = [_signature(n) for n in test_nodes]
        common = set(ptr_seq) & set(test_seq)
        ptr_common = [s for s in ptr_seq if s in common]
        test_common = [s for s in test_seq if s in common]
        # De-duplicate preserving order
        ptr_order = list(dict.fromkeys(ptr_common))
        test_order = list(dict.fromkeys(test_common))

        if ptr_order != test_order and set(ptr_order) == set(test_order):
            return [
                Variance(
                    dimension="layout",
                    severity="major",
                    location="element order",
                    ptr_value=" > ".join(self._short_sig(s) for s in ptr_order),
                    test_value=" > ".join(self._short_sig(s) for s in test_order),
                    description=(
                        "Element ordering differs between PTR and Test "
                        "(same elements, different sequence)."
                    ),
                    category="reordered",
                )
            ]
        return []

    @staticmethod
    def _short_sig(sig: str) -> str:
        tag, _, text = sig.partition("|")
        text = text.strip()
        if text:
            return f"{tag}:{text[:15]}"
        return tag

    # -- hierarchy ------------------------------------------------------

    def _hierarchy(self, ptr: ParsedHTML, test: ParsedHTML) -> List[Variance]:
        variances: List[Variance] = []
        ptr_headings = [(n.tag, _norm_text(n.visible_text(), 30)) for n in ptr.find_all(*HEADING_TAGS)]
        test_headings = [(n.tag, _norm_text(n.visible_text(), 30)) for n in test.find_all(*HEADING_TAGS)]

        # Match headings by text; report level changes.
        test_by_text = {text: tag for tag, text in test_headings}
        for tag, text in ptr_headings:
            if not text:
                continue
            if text in test_by_text and test_by_text[text] != tag:
                variances.append(
                    Variance(
                        dimension="layout",
                        severity="major",
                        location=f"heading \"{text}\"",
                        ptr_value=tag,
                        test_value=test_by_text[text],
                        description=(
                            f"Heading level changed for \"{text}\": "
                            f"PTR uses <{tag}> but Test uses "
                            f"<{test_by_text[text]}>."
                        ),
                        category="hierarchy",
                    )
                )
        return variances
