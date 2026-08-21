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

from creative_compare_agent.validators.master import (
    MasterValidationAgent,
    creative_from_string,
)

_SAMPLES_DIR = os.path.join(_PROJECT_ROOT, "samples")

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
        ptr_content = _resolve_input("ptr_text", "ptr_file")
        test_content = _resolve_input("test_text", "test_file")

        if not ptr_content.strip() or not test_content.strip():
            return (
                render_template(
                    "index.html",
                    ptr_text=ptr_content,
                    test_text=test_content,
                    error=(
                        "Please provide BOTH a PTR (master) and a Test input — "
                        "paste content or upload a file for each."
                    ),
                ),
                400,
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

    return app


# Module-level app for `flask run` / test_client / gunicorn.
app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("FLASK_DEBUG")))
