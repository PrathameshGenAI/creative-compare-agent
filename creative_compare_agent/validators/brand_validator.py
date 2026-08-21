"""Brand compliance auditing from parsed HTML/CSS.

Extracts and compares against the PTR baseline:

* Typography — font-family, font-size, font-weight, font-style
* Color usage — hex / rgb(a) colors (text color + background)
* Logo presence & placement — <img> whose alt/src contains 'logo'
* CTA styling — button/anchor text, colors, background

Findings are emitted as :class:`Variance` objects in the ``brand``
dimension.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from .htmldoc import Node, ParsedHTML, parse_html
from .models import Variance


_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_RGB_RE = re.compile(r"rgba?\([^)]*\)")

_FONT_PROPS = ("font-family", "font-size", "font-weight", "font-style")

_CTA_KEYWORDS = ("shop", "buy", "get", "start", "sign up", "signup", "join",
                 "learn more", "order", "subscribe", "download", "try",
                 "claim", "redeem", "book", "register")


def _norm_text(text: str, limit: int = 40) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()[:limit]


def _normalize_color(value: str) -> str:
    v = value.strip().lower()
    # Expand 3-digit hex to 6-digit for stable comparison.
    m = re.fullmatch(r"#([0-9a-f])([0-9a-f])([0-9a-f])", v)
    if m:
        return "#" + "".join(c * 2 for c in m.groups())
    # Normalize rgb spacing
    if v.startswith("rgb"):
        nums = re.findall(r"[\d.]+", v)
        if nums:
            return ("rgba(" if v.startswith("rgba") else "rgb(") + ",".join(nums) + ")"
    return v


def _extract_colors(text: str) -> List[str]:
    colors = []
    for m in _HEX_RE.findall(text or ""):
        colors.append(_normalize_color(m))
    for m in _RGB_RE.findall(text or ""):
        colors.append(_normalize_color(m))
    return colors


class BrandComplianceAuditor:
    """Audit typography, color, logo, and CTA compliance vs PTR."""

    dimension = "brand"

    def validate(self, ptr_html: str, test_html: str) -> List[Variance]:
        ptr = parse_html(ptr_html)
        test = parse_html(test_html)
        variances: List[Variance] = []

        variances.extend(self._typography(ptr, test))
        variances.extend(self._colors(ptr, test))
        variances.extend(self._logo(ptr, test))
        variances.extend(self._cta(ptr, test))

        return variances

    # ------------------------------------------------------------------
    # Typography
    # ------------------------------------------------------------------

    def _font_profile(self, doc: ParsedHTML) -> Dict[str, Counter]:
        profile: Dict[str, Counter] = {p: Counter() for p in _FONT_PROPS}
        # Inline styles
        for node in doc.iter_nodes():
            style = node.style
            for prop in _FONT_PROPS:
                if prop in style:
                    profile[prop][style[prop].strip().lower()] += 1
        # CSS blocks (coarse: pull declarations regardless of selector)
        css = doc.all_css
        for prop in _FONT_PROPS:
            for m in re.findall(prop + r"\s*:\s*([^;{}]+)", css, flags=re.IGNORECASE):
                profile[prop][m.strip().lower()] += 1
        return profile

    def _typography(self, ptr: ParsedHTML, test: ParsedHTML) -> List[Variance]:
        variances: List[Variance] = []
        ptr_prof = self._font_profile(ptr)
        test_prof = self._font_profile(test)
        for prop in _FONT_PROPS:
            ptr_values = set(ptr_prof[prop])
            test_values = set(test_prof[prop])
            missing = ptr_values - test_values
            extra = test_values - ptr_values
            if not missing and not extra:
                continue
            severity = "major" if prop == "font-family" else "minor"
            variances.append(
                Variance(
                    dimension="brand",
                    severity=severity,
                    location=prop,
                    ptr_value=", ".join(sorted(ptr_values)) or "—",
                    test_value=", ".join(sorted(test_values)) or "—",
                    description=self._font_desc(prop, missing, extra),
                    category="font",
                )
            )
        return variances

    @staticmethod
    def _font_desc(prop, missing, extra) -> str:
        parts = []
        if missing:
            parts.append(f"PTR uses {sorted(missing)} not found in Test")
        if extra:
            parts.append(f"Test introduces {sorted(extra)} not in PTR")
        return f"Typography difference in {prop}: " + "; ".join(parts) + "."

    # ------------------------------------------------------------------
    # Colors
    # ------------------------------------------------------------------

    def _color_set(self, doc: ParsedHTML) -> Counter:
        counter: Counter = Counter()
        for node in doc.iter_nodes():
            style_str = node.get("style", "")
            for c in _extract_colors(style_str):
                counter[c] += 1
            # legacy attributes
            for attr in ("bgcolor", "color"):
                if node.get(attr):
                    counter[_normalize_color(node.get(attr))] += 1
        for c in _extract_colors(doc.all_css):
            counter[c] += 1
        return counter

    def _colors(self, ptr: ParsedHTML, test: ParsedHTML) -> List[Variance]:
        variances: List[Variance] = []
        ptr_colors = self._color_set(ptr)
        test_colors = self._color_set(test)
        ptr_set = set(ptr_colors)
        test_set = set(test_colors)

        missing = ptr_set - test_set
        extra = test_set - ptr_set

        for color in sorted(missing):
            variances.append(
                Variance(
                    dimension="brand",
                    severity="major",
                    location=f"color {color}",
                    ptr_value=color,
                    test_value="(absent)",
                    description=(
                        f"Brand color {color} used in PTR is missing from Test."
                    ),
                    category="color",
                )
            )
        for color in sorted(extra):
            variances.append(
                Variance(
                    dimension="brand",
                    severity="major",
                    location=f"color {color}",
                    ptr_value="(absent)",
                    test_value=color,
                    description=(
                        f"Non-baseline color {color} appears in Test but not PTR."
                    ),
                    category="color",
                )
            )
        return variances

    # ------------------------------------------------------------------
    # Logo
    # ------------------------------------------------------------------

    def _logos(self, doc: ParsedHTML) -> List[Node]:
        logos = []
        for node in doc.find_all("img"):
            alt = node.get("alt", "").lower()
            src = node.get("src", "").lower()
            if "logo" in alt or "logo" in src:
                logos.append(node)
        return logos

    def _ancestor_path(self, node: Node) -> str:
        path = []
        cur = node.parent
        depth = 0
        while cur is not None and cur.tag != "#document" and depth < 5:
            path.append(cur.tag)
            cur = cur.parent
            depth += 1
        return " < ".join(path) if path else "(root)"

    def _logo(self, ptr: ParsedHTML, test: ParsedHTML) -> List[Variance]:
        variances: List[Variance] = []
        ptr_logos = self._logos(ptr)
        test_logos = self._logos(test)

        if ptr_logos and not test_logos:
            variances.append(
                Variance(
                    dimension="brand",
                    severity="critical",
                    location="logo",
                    ptr_value=f"{len(ptr_logos)} logo(s)",
                    test_value="none",
                    description="Logo present in PTR is missing from Test.",
                    category="logo",
                )
            )
            return variances
        if not ptr_logos and test_logos:
            variances.append(
                Variance(
                    dimension="brand",
                    severity="major",
                    location="logo",
                    ptr_value="none",
                    test_value=f"{len(test_logos)} logo(s)",
                    description="Test contains a logo not present in PTR.",
                    category="logo",
                )
            )
            return variances

        if ptr_logos and test_logos:
            ptr_path = self._ancestor_path(ptr_logos[0])
            test_path = self._ancestor_path(test_logos[0])
            if ptr_path != test_path:
                variances.append(
                    Variance(
                        dimension="brand",
                        severity="major",
                        location="logo placement",
                        ptr_value=ptr_path,
                        test_value=test_path,
                        description=(
                            "Logo placement differs: PTR container path "
                            f"'{ptr_path}' vs Test '{test_path}'."
                        ),
                        category="logo",
                    )
                )
            # alt text change
            ptr_alt = ptr_logos[0].get("alt", "")
            test_alt = test_logos[0].get("alt", "")
            if ptr_alt != test_alt:
                variances.append(
                    Variance(
                        dimension="brand",
                        severity="minor",
                        location="logo alt text",
                        ptr_value=ptr_alt or "—",
                        test_value=test_alt or "—",
                        description=(
                            f"Logo alt text differs: PTR '{ptr_alt}' vs "
                            f"Test '{test_alt}'."
                        ),
                        category="logo",
                    )
                )
        return variances

    # ------------------------------------------------------------------
    # CTA
    # ------------------------------------------------------------------

    def _ctas(self, doc: ParsedHTML) -> List[Node]:
        ctas = []
        for node in doc.find_all("a", "button"):
            text = node.visible_text().lower()
            classes = node.get("class", "").lower()
            is_button_tag = node.tag == "button"
            looks_cta = (
                is_button_tag
                or "button" in classes
                or "btn" in classes
                or "cta" in classes
                or any(k in text for k in _CTA_KEYWORDS)
            )
            if looks_cta:
                ctas.append(node)
        return ctas

    def _cta_style(self, node: Node) -> Dict[str, str]:
        style = node.style
        return {
            "text": node.visible_text(),
            "background": _normalize_color(
                style.get("background-color", style.get("background", ""))
            ),
            "color": _normalize_color(style.get("color", "")),
            "href": node.get("href", ""),
        }

    def _cta(self, ptr: ParsedHTML, test: ParsedHTML) -> List[Variance]:
        variances: List[Variance] = []
        ptr_ctas = self._ctas(ptr)
        test_ctas = self._ctas(test)

        if ptr_ctas and not test_ctas:
            variances.append(
                Variance(
                    dimension="brand",
                    severity="critical",
                    location="CTA",
                    ptr_value=f"{len(ptr_ctas)} CTA(s)",
                    test_value="none",
                    description="Call-to-action present in PTR is missing from Test.",
                    category="cta",
                )
            )
            return variances

        # Pair CTAs positionally (by index) for style comparison.
        for idx, ptr_node in enumerate(ptr_ctas):
            if idx >= len(test_ctas):
                break
            test_node = test_ctas[idx]
            p = self._cta_style(ptr_node)
            t = self._cta_style(test_node)
            loc = f"CTA #{idx + 1}"

            if _norm_text(p["text"]) != _norm_text(t["text"]):
                variances.append(
                    Variance(
                        dimension="brand",
                        severity="major",
                        location=f"{loc} text",
                        ptr_value=p["text"] or "—",
                        test_value=t["text"] or "—",
                        description=(
                            f"CTA text changed: PTR \"{p['text']}\" vs "
                            f"Test \"{t['text']}\"."
                        ),
                        category="cta",
                    )
                )
            if p["background"] != t["background"] and (p["background"] or t["background"]):
                variances.append(
                    Variance(
                        dimension="brand",
                        severity="major",
                        location=f"{loc} background",
                        ptr_value=p["background"] or "—",
                        test_value=t["background"] or "—",
                        description=(
                            f"CTA background color changed: PTR "
                            f"'{p['background']}' vs Test '{t['background']}'."
                        ),
                        category="cta",
                    )
                )
            if p["color"] != t["color"] and (p["color"] or t["color"]):
                variances.append(
                    Variance(
                        dimension="brand",
                        severity="minor",
                        location=f"{loc} text color",
                        ptr_value=p["color"] or "—",
                        test_value=t["color"] or "—",
                        description=(
                            f"CTA text color changed: PTR '{p['color']}' vs "
                            f"Test '{t['color']}'."
                        ),
                        category="cta",
                    )
                )
        return variances
