"""Master orchestrator for creative validation.

``MasterValidationAgent`` accepts two creatives (PTR master + Test),
auto-detects their kind (plain text vs HTML, optionally image via OCR
when available), runs the appropriate sub-validators, and merges their
findings into a single :class:`ValidationReport`.

Design goals:
* Fully local, deterministic, no paid APIs.
* HTML parsing via the stdlib (:mod:`html.parser`) — no hard deps.
* Optional OCR only if ``pytesseract`` + ``PIL`` import cleanly.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .htmldoc import parse_html
from .models import ValidationReport, Variance
from .text_validator import TextAccuracyValidator
from .layout_validator import LayoutAlignmentInspector
from .brand_validator import BrandComplianceAuditor


# ---------------------------------------------------------------------------
# Optional OCR capability probe (never hard-fails)
# ---------------------------------------------------------------------------

def _ocr_available() -> bool:
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        return False
    return True


OCR_AVAILABLE = _ocr_available()

_HTML_EXTS = {".html", ".htm", ".xhtml"}
_TEXT_EXTS = {".txt", ".text", ".md"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

_HTML_SNIFF = re.compile(r"<\s*(html|body|div|p|table|span|img|a|h[1-6]|head)\b", re.IGNORECASE)


@dataclass
class Creative:
    """A normalized creative input."""

    label: str
    kind: str  # 'text' or 'html'
    raw: str  # original content (HTML source or plain text)
    text: str  # extracted visible/plain text
    note: Optional[str] = None  # e.g. OCR or degradation note


def _detect_kind(content: str, ext: str) -> str:
    ext = (ext or "").lower()
    if ext in _HTML_EXTS:
        return "html"
    if ext in _TEXT_EXTS:
        return "text"
    # Sniff content
    if _HTML_SNIFF.search(content or ""):
        return "html"
    return "text"


def load_creative(path: str, label: Optional[str] = None) -> Creative:
    """Load a creative from a file path, auto-detecting its kind."""
    ext = os.path.splitext(path)[1].lower()
    lbl = label or os.path.basename(path)

    if ext in _IMAGE_EXTS:
        return _load_image(path, lbl)

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    return creative_from_string(content, label=lbl, ext=ext)


def creative_from_string(
    content: str, label: str = "creative", ext: str = "", kind: Optional[str] = None
) -> Creative:
    """Build a Creative from an in-memory string."""
    detected = kind or _detect_kind(content, ext)
    if detected == "html":
        parsed = parse_html(content)
        return Creative(
            label=label, kind="html", raw=content, text=parsed.visible_text()
        )
    return Creative(label=label, kind="text", raw=content, text=content)


def _load_image(path: str, label: str) -> Creative:
    """Load an image creative via OCR if available; else degrade gracefully."""
    if not OCR_AVAILABLE:
        note = (
            "Image input received but OCR is unavailable (pytesseract/PIL not "
            "importable). Text extraction skipped — install pytesseract + "
            "Pillow + the tesseract binary to enable image validation."
        )
        return Creative(label=label, kind="text", raw="", text="", note=note)
    try:
        import pytesseract
        from PIL import Image

        text = pytesseract.image_to_string(Image.open(path))
        return Creative(
            label=label,
            kind="text",
            raw=text,
            text=text,
            note="Text extracted from image via OCR (pytesseract).",
        )
    except Exception as exc:  # pragma: no cover - depends on runtime binary
        return Creative(
            label=label,
            kind="text",
            raw="",
            text="",
            note=f"Image OCR failed ({exc}); text extraction skipped.",
        )


class MasterValidationAgent:
    """Orchestrate the three validators and merge into one report.

    Usage::

        agent = MasterValidationAgent(ptr, test)   # Creative or str
        report = agent.validate()
        print(report.to_markdown())
        print(report.to_json())
    """

    def __init__(
        self,
        ptr,
        test,
        ptr_label: Optional[str] = None,
        test_label: Optional[str] = None,
    ) -> None:
        self.ptr = self._coerce(ptr, ptr_label or "PTR")
        self.test = self._coerce(test, test_label or "Test")
        self.text_validator = TextAccuracyValidator()
        self.layout_inspector = LayoutAlignmentInspector()
        self.brand_auditor = BrandComplianceAuditor()

    @staticmethod
    def _coerce(value, default_label: str) -> Creative:
        if isinstance(value, Creative):
            return value
        if isinstance(value, str):
            return creative_from_string(value, label=default_label)
        raise TypeError(
            f"Expected Creative or str, got {type(value).__name__}"
        )

    # ------------------------------------------------------------------

    def validate(self) -> ValidationReport:
        report = ValidationReport(
            ptr_label=self.ptr.label,
            test_label=self.test.label,
        )

        # Carry over any input notes (e.g. OCR degradation messages).
        for creative in (self.ptr, self.test):
            if creative.note:
                report.notes.append(f"[{creative.label}] {creative.note}")

        run_html = self.ptr.kind == "html" and self.test.kind == "html"

        # --- Text accuracy (always) ---
        report.dimensions_run.append("text")
        report.extend(
            self.text_validator.validate(self.ptr.text, self.test.text)
        )

        if run_html:
            # --- Layout & alignment ---
            report.dimensions_run.append("layout")
            report.extend(
                self.layout_inspector.validate(self.ptr.raw, self.test.raw)
            )
            # --- Brand compliance ---
            report.dimensions_run.append("brand")
            report.extend(
                self.brand_auditor.validate(self.ptr.raw, self.test.raw)
            )
        else:
            mixed = {self.ptr.kind, self.test.kind}
            if mixed == {"html", "text"}:
                report.notes.append(
                    "One input is HTML and the other is plain text; only text "
                    "accuracy was validated. Provide both as HTML to run "
                    "layout and brand checks."
                )
            else:
                report.notes.append(
                    "Both inputs are plain text; only text accuracy was "
                    "validated (layout and brand checks require HTML)."
                )

        return report

    # Convenience passthroughs -----------------------------------------

    def to_markdown(self) -> str:
        return self.validate().to_markdown()

    def to_json(self) -> str:
        return self.validate().to_json()


def validate_files(
    ptr_path: str,
    test_path: str,
) -> ValidationReport:
    """Load two files and run the master validation."""
    ptr = load_creative(ptr_path)
    test = load_creative(test_path)
    return MasterValidationAgent(ptr, test).validate()
