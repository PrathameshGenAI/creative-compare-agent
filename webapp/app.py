"""Self-contained Flask web UI for the Master Creative Validation Agent.

Reuses the existing validators (does NOT reimplement validation logic):
- ``creative_from_string`` to build Creative objects (auto-detect html/text)
- ``MasterValidationAgent(...).validate()`` to produce a ValidationReport

Launch::

    cd /workspace/creative-compare-agent
    python webapp/app.py            # binds 0.0.0.0:5000 (or $PORT)

The report is rendered as HTML in the browser and can be downloaded as
Markdown or JSON. Fully local; no external CDNs; no API keys.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from typing import Dict, Optional, Tuple

# --- Make the project importable regardless of the current working dir ------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from flask import (
    Flask,
    Response,
    abort,
    render_template,
    request,
)
from werkzeug.exceptions import RequestEntityTooLarge

from creative_compare_agent.validators.master import (
    MasterValidationAgent,
    Creative,
    creative_from_string,
)
from email_normalize import (
    strip_outlook_header_div,
    extract_names_from_raw_html,
    normalize_email_text as _normalize_email_text,
    normalize_html_source as _normalize_html_source,
)


def _replace_creative_text(creative: Creative, new_text: str) -> Creative:
    """Return a copy of *creative* with ``text`` replaced."""
    from dataclasses import replace
    return replace(creative, text=new_text)


_SAMPLES_DIR = os.path.join(_PROJECT_ROOT, "samples")

# Per-creative character cap, measured AFTER sanitizing (stripping inline
# base64 blobs etc.). Real marketing emails are well under this once the
# embedded image data is removed.
_MAX_INPUT_CHARS = 12_000_000

# Matches inline base64 payloads inside data: URIs on a SINGLE segment (no
# newlines) — line-wrapped variants are handled by the line scanner below.
_DATA_URI_RE = re.compile(r"data:([^;,]*);base64,[A-Za-z0-9+/=]+")
# <script>/<style> bodies add size without affecting the visible creative.
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
# A single unbroken very-long token (e.g. an inline encoded run).
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=]{500,}")
# Set of characters that make up a base64 line (plus MIME soft-wrap chars).
_B64_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")

# Regex to locate the outermost <html> block inside a pasted forwarded email.
_HTML_TAG_RE = re.compile(r"<html[\s>]", re.IGNORECASE)

# Forwarded-message / quoted-email preamble patterns (plain-text inputs).
# We look for lines that look like email headers immediately before the
# creative content starts and strip them (along with any leading blank lines).
_FWD_HEADER_RE = re.compile(
    r"""
    (?:^|\n)                        # start of string or newline
    [ \t]*                          # optional leading whitespace
    (?:
        -{3,}[ \t]*(?:forwarded|original|begin forwarded)[ \t\w]*-{3,}  # --- Forwarded message ---
      | from\s*:\s*\S               # From: header
      | to\s*:\s*\S                 # To: header
      | cc\s*:\s*\S                 # Cc: header
      | date\s*:\s*\S               # Date: header
      | sent\s*:\s*\S               # Sent: header
      | subject\s*:\s*\S            # Subject: header
      | reply-to\s*:\s*\S           # Reply-To: header
    )
    [^\n]*                          # rest of that line
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _strip_base64_lines(text: str) -> str:
    """Linear, backtracking-free scan that drops long base64 line-runs.

    Marketing emails (esp. raw .eml MIME source) embed images as base64
    attachments wrapped at ~76 chars per line. A run of many consecutive
    lines that are almost entirely base64 characters is an encoded asset,
    never visible copy. We collapse each such run into a single marker.
    """
    lines = text.splitlines()
    out = []
    run = 0
    for ln in lines:
        stripped = ln.strip()
        # A "base64-ish" line: reasonably long and ≥ the vast majority
        # base64 characters.
        is_b64 = (
            len(stripped) >= 60
            and sum(c in _B64_CHARS for c in stripped) >= 0.95 * len(stripped)
        )
        if is_b64:
            run += 1
            continue
        if run:
            # Only treat as an asset if the run was substantial (>= 8 lines,
            # i.e. > ~600 encoded bytes); otherwise keep the lines.
            if run >= 8:
                out.append("[EMBEDDED_ASSET_STRIPPED]")
            run = 0
        out.append(ln)
    if run >= 8:
        out.append("[EMBEDDED_ASSET_STRIPPED]")
    return "\n".join(out)

# Hard cap on the whole upload body so pathological uploads are rejected by
# Flask/Werkzeug before we read them into memory. Generous because real
# marketing emails often inline base64 images (many MB); we strip those in
# ``_sanitize`` right after, then enforce the post-sanitize character cap.
_MAX_CONTENT_BYTES = 64 * 1024 * 1024

# In-memory cache of the most recent reports for download links.
# Keyed by an opaque token; local single-user app so this is sufficient.
_REPORT_CACHE: Dict[str, Dict[str, str]] = {}
_CACHE_MAX = 50


def _read_sample(name: str) -> str:
    path = os.path.join(_SAMPLES_DIR, name)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _extract_creative_content(content: str) -> Tuple[str, str]:
    """Strip forwarded-email wrapper content and return only the creative.

    When a user pastes an email forwarded by a colleague (e.g. "Girish
    Pawar forwarded this"), the pasted text contains email-client metadata
    (From/To/Date/Subject headers, forwarded-message banners, quoted reply
    chains) *around* the actual HTML creative. Those wrapper lines are not
    part of the creative and would produce spurious variances.

    Strategy:
    - If the content contains an ``<html`` tag, extract from that tag to
      the matching ``</html>`` close tag. Everything before or after
      (forwarded-message headers, the email client's own chrome) is
      discarded.
    - For plain-text content, strip leading lines that match common
      forwarded/quoted email header patterns (From:, To:, Date:, Subject:,
      the ``--- Forwarded message ---`` banner, etc.) and any blank lines
      that follow them.

    Returns (cleaned_content, note) where note is a non-empty string if
    content was trimmed so a UI note can be shown.
    """
    if not content:
        return content, ""

    # --- HTML path: extract the outermost <html>…</html> block -----------
    m = _HTML_TAG_RE.search(content)
    if m:
        start = m.start()
        # Find the matching </html> closing tag.
        close = content.lower().rfind("</html>")
        if close != -1:
            end = close + len("</html>")
        else:
            # No closing tag — take everything from <html> onwards.
            end = len(content)

        extracted = content[start:end]
        stripped_chars = len(content) - len(extracted)
        if stripped_chars > 20:
            return extracted, (
                f"Forwarded-email wrapper stripped ({stripped_chars:,} chars of "
                "non-creative content removed before the <html> block)."
            )
        return extracted, ""

    # --- Plain-text path: strip leading email-header lines ---------------
    lines = content.splitlines()
    first_content_line = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        # Skip the dashed forwarded-message banner and any consecutive header
        # lines that immediately follow it.
        if _FWD_HEADER_RE.match("\n" + line) or (
            line.strip() == "" and first_content_line == i
        ):
            first_content_line = i + 1
        else:
            # A non-empty, non-header line: stop scanning.
            if line.strip():
                break
        i += 1

    if first_content_line > 0:
        cleaned = "\n".join(lines[first_content_line:])
        removed = len(content) - len(cleaned)
        return cleaned, (
            f"Forwarded-email header lines stripped "
            f"({first_content_line} line(s), {removed:,} chars removed)."
        )
    return content, ""


def _sanitize(content: str) -> str:
    """Strip heavyweight, comparison-irrelevant blobs from the raw input.

    - Inline base64 data URIs (embedded images/fonts) -> short marker.
    - <script> bodies -> empty tag.
    This preserves the visible/structural content the validators care about
    while cutting megabytes of encoded noise that would otherwise blow past
    the size cap and slow validation.
    """
    if not content:
        return content
    # 1) Drop line-wrapped base64 runs (MIME attachments, wrapped data URIs).
    content = _strip_base64_lines(content)
    # 2) Inline single-line data: URIs.
    content = _DATA_URI_RE.sub(
        lambda m: "data:%s;base64,EMBEDDED_ASSET_STRIPPED" % (m.group(1) or ""),
        content,
    )
    # 3) <script>/<style> bodies.
    content = _SCRIPT_RE.sub("<script></script>", content)
    content = _STYLE_RE.sub("<style></style>", content)
    # 4) Any single very long unbroken token.
    content = _LONG_TOKEN_RE.sub("[EMBEDDED_ASSET_STRIPPED]", content)
    return content


def _extract_pdf_text(data: bytes) -> str:
    """Extract visible text from a PDF's bytes.

    Marketing creatives are frequently exported as PDF. Reading the raw PDF
    bytes as text yields megabytes of binary garbage, so we must parse the
    document and pull the text layer instead.
    """
    import io

    try:
        from pypdf import PdfReader
    except Exception:  # pragma: no cover - dependency guaranteed in prod
        raise RuntimeError(
            "PDF support requires the 'pypdf' package to be installed."
        )
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            # Skip a page we can't parse rather than failing the whole doc.
            continue
    return "\n".join(pages)


def _looks_like_pdf(filename: str, data: bytes) -> bool:
    """Detect a PDF by extension or magic header (%PDF-)."""
    if filename and filename.lower().endswith(".pdf"):
        return True
    return data[:5] == b"%PDF-"


def _resolve_input(text_field: str, file_field: str) -> str:
    """Return the effective content: uploaded file takes precedence.

    Handles PDF uploads by extracting their text layer; everything else is
    decoded as UTF-8 text.
    """
    upload = request.files.get(file_field)
    if upload and upload.filename:
        data = upload.read()
        if not isinstance(data, (bytes, bytearray)):
            return str(data)
        if _looks_like_pdf(upload.filename, data):
            return _extract_pdf_text(bytes(data))
        return data.decode("utf-8", errors="replace")
    return request.form.get(text_field, "") or ""


def _cache_report(md: str, js: str) -> str:
    token = uuid.uuid4().hex
    # Simple bound to avoid unbounded growth.
    if len(_REPORT_CACHE) >= _CACHE_MAX:
        _REPORT_CACHE.pop(next(iter(_REPORT_CACHE)))
    _REPORT_CACHE[token] = {"md": md, "json": js}
    return token


_SEVERITY_ORDER = ("critical", "major", "minor")


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = _MAX_CONTENT_BYTES
    # Werkzeug 3.1+ caps non-file form fields (e.g. pasted textarea content)
    # at 500KB by default via MAX_FORM_MEMORY_SIZE. Real pasted emails with
    # inline base64 images easily exceed that, so raise it to match the body
    # cap; the post-sanitize character cap is the real guardrail.
    app.config["MAX_FORM_MEMORY_SIZE"] = _MAX_CONTENT_BYTES

    def _render_error(message, detail="", heading="", status=200):
        """Render the friendly error page (never a bare 500)."""
        return (
            render_template(
                "error.html",
                message=message,
                detail=detail,
                heading=heading,
            ),
            status,
        )

    @app.route("/", methods=["GET"])
    def index():
        ptr_text = ""
        test_text = ""
        if request.args.get("sample"):
            ptr_text = _read_sample("ptr_email.html")
            test_text = _read_sample("test_email.html")
        return render_template(
            "index.html",
            ptr_text=ptr_text,
            test_text=test_text,
            sample_loaded=bool(request.args.get("sample")),
        )

    @app.route("/validate", methods=["POST"])
    def validate():
        # Hard safety net: no matter what goes wrong inside, the user gets a
        # friendly HTTP 200 page instead of a bare 500 from gunicorn.
        try:
            # 1) Resolve raw input (file upload wins over textarea).
            ptr_raw = _resolve_input("ptr_text", "ptr_file")
            test_raw = _resolve_input("test_text", "test_file")

            # 2) Strip forwarded-email wrapper content (From:/To:/Date: headers,
            #    "--- Forwarded message ---" banners, etc.) so only the actual
            #    HTML creative is validated — not metadata added by the sender.
            ptr_raw, ptr_fwd_note = _extract_creative_content(ptr_raw)
            test_raw, test_fwd_note = _extract_creative_content(test_raw)

            # 3) Extract recipient names from raw HTML *before* stripping the
            #    Outlook header div, so they can still be masked in the body text
            #    (personalization: "Girish" / "Pawar" / account numbers).
            ptr_names = extract_names_from_raw_html(ptr_raw)
            test_names = extract_names_from_raw_html(test_raw)

            # 4) Strip Outlook in-body header divs (From:/Sent:/To:/Subject:
            #    injected as an HTML element inside <body> by mail clients).
            ptr_raw = strip_outlook_header_div(ptr_raw)
            test_raw = strip_outlook_header_div(test_raw)

            # 5) Strip base64 blobs and script bodies.
            ptr_content = _sanitize(ptr_raw)
            test_content = _sanitize(test_raw)

            if not ptr_content.strip() or not test_content.strip():
                return (
                    render_template(
                        "index.html",
                        ptr_text=ptr_content[: _MAX_INPUT_CHARS],
                        test_text=test_content[: _MAX_INPUT_CHARS],
                        error=(
                            "Please provide BOTH a PTR (master) and a Test input — "
                            "paste content or upload a file for each."
                        ),
                    ),
                    400,
                )

            # Reject absurdly large inputs before processing.
            if (
                len(ptr_content) > _MAX_INPUT_CHARS
                or len(test_content) > _MAX_INPUT_CHARS
            ):
                return _render_error(
                    message=(
                        "One or both creatives are too large to validate "
                        f"(limit is {_MAX_INPUT_CHARS:,} characters each). "
                        "Please trim the input or split it into smaller pieces."
                    ),
                    detail=(
                        f"PTR: {len(ptr_content):,} chars, "
                        f"Test: {len(test_content):,} chars."
                    ),
                    heading="Input too large",
                    status=200,
                )

            # 6) Normalize personalization tokens and recipient names in the raw
            #    HTML source so layout/brand validators see matching element
            #    signatures (PTR <%= X %> and Test "6789"/"Girish" both become
            #    ‹PERSONALIZED›).
            ptr_content = _normalize_html_source(ptr_content, ptr_names)
            test_content = _normalize_html_source(test_content, test_names)

            ptr_creative = creative_from_string(ptr_content, label="PTR")
            test_creative = creative_from_string(test_content, label="Test")

            # 7) Normalize extracted text: strip any residual email-client header
            #    lines and mask rendered recipient name words.
            ptr_creative = _replace_creative_text(
                ptr_creative, _normalize_email_text(ptr_creative.text, ptr_names)
            )
            test_creative = _replace_creative_text(
                test_creative, _normalize_email_text(test_creative.text, test_names)
            )

            report = MasterValidationAgent(ptr_creative, test_creative).validate()

            # Surface any forwarded-email stripping notes in the report.
            if ptr_fwd_note:
                report.notes.insert(0, f"[PTR] {ptr_fwd_note}")
            if test_fwd_note:
                report.notes.insert(0, f"[Test] {test_fwd_note}")

            token = _cache_report(report.to_markdown(), report.to_json())

            return render_template(
                "report.html",
                report=report,
                variances=report.sorted_variances,
                by_dimension=report.counts_by_dimension(),
                by_severity=report.counts_by_severity(),
                passed=report.passed,
                token=token,
                severity_order=_SEVERITY_ORDER,
            )
        except RequestEntityTooLarge:
            return _render_error(
                message=(
                    "The uploaded files are too large. Please keep the total "
                    "upload under 64 MB."
                ),
                heading="Upload too large",
                status=200,
            )
        except Exception as exc:  # noqa: BLE001 — deliberate catch-all
            return _render_error(
                message=(
                    "Something went wrong while comparing these creatives. "
                    "Please try again, or reduce the input size."
                ),
                detail=f"{type(exc).__name__}: {exc}",
                heading="Validation failed",
                status=200,
            )

    @app.route("/download/<token>.<fmt>", methods=["GET"])
    def download(token: str, fmt: str):
        entry = _REPORT_CACHE.get(token)
        if not entry:
            abort(404, "Report expired or not found — please re-run validation.")
        if fmt == "md":
            return Response(
                entry["md"],
                mimetype="text/markdown",
                headers={
                    "Content-Disposition": "attachment; filename=validation_report.md"
                },
            )
        if fmt == "json":
            return Response(
                entry["json"],
                mimetype="application/json",
                headers={
                    "Content-Disposition": "attachment; filename=validation_report.json"
                },
            )
        abort(404, "Unknown format.")

    # --- Global safety nets: never surface a bare 500 --------------------

    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(_exc):
        return _render_error(
            message=(
                "The uploaded files are too large. Please keep the total "
                "upload under 64 MB."
            ),
            heading="Upload too large",
            status=200,
        )

    @app.errorhandler(500)
    def _internal_error(exc):
        return _render_error(
            message=(
                "The server hit an unexpected error. Please try again with "
                "smaller inputs."
            ),
            detail=f"{type(exc).__name__}: {exc}",
            heading="Internal error",
            status=200,
        )

    @app.errorhandler(Exception)
    def _unhandled(exc):
        # Let Flask handle normal HTTP exceptions (404, etc.) as usual.
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return exc
        return _render_error(
            message=(
                "The server hit an unexpected error. Please try again with "
                "smaller inputs."
            ),
            detail=f"{type(exc).__name__}: {exc}",
            heading="Internal error",
            status=200,
        )

    return app


# Module-level app for `flask run` / test_client / gunicorn.
app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("FLASK_DEBUG")))
