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
from typing import Dict, Optional

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
    creative_from_string,
)

_SAMPLES_DIR = os.path.join(_PROJECT_ROOT, "samples")

# Per-creative character cap, measured AFTER sanitizing (stripping inline
# base64 blobs etc.). Real marketing emails are well under this once the
# embedded image data is removed.
_MAX_INPUT_CHARS = 2_000_000

# Matches inline base64 payloads inside data: URIs (images/fonts embedded
# directly in the HTML). These can be hundreds of KB each and are pure noise
# for a text/layout/brand comparison, so we replace them with a short marker
# that still records "an embedded asset exists here".
_DATA_URI_RE = re.compile(r"data:([^;,]*);base64,[A-Za-z0-9+/=\s]+")
# Matches <script>...</script> bodies (tracking/analytics blobs) which also
# add size without affecting the visible creative.
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)

# Hard cap on the whole upload body (~8MB) so oversized uploads are rejected
# by Flask/Werkzeug before we ever read them into memory.
_MAX_CONTENT_BYTES = 8 * 1024 * 1024

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
    content = _DATA_URI_RE.sub(
        lambda m: "data:%s;base64,EMBEDDED_ASSET_STRIPPED" % (m.group(1) or ""),
        content,
    )
    content = _SCRIPT_RE.sub("<script></script>", content)
    return content


def _resolve_input(text_field: str, file_field: str) -> str:
    """Return the effective content: uploaded file takes precedence."""
    upload = request.files.get(file_field)
    if upload and upload.filename:
        data = upload.read()
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data)
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
            ptr_content = _sanitize(_resolve_input("ptr_text", "ptr_file"))
            test_content = _sanitize(_resolve_input("test_text", "test_file"))

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

            ptr_creative = creative_from_string(ptr_content, label="PTR")
            test_creative = creative_from_string(test_content, label="Test")
            report = MasterValidationAgent(ptr_creative, test_creative).validate()

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
                    "upload under 8 MB."
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
                "upload under 8 MB."
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
