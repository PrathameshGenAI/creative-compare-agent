"""WSGI entrypoint for production servers (gunicorn/uwsgi).

Exposes the Flask application as ``app`` so process managers can run::

    gunicorn wsgi:app --bind 0.0.0.0:$PORT

The web app lives in ``webapp/app.py``; we ensure the project root is on
``sys.path`` so ``webapp`` is importable regardless of the working dir.
"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Re-export the module-level Flask app created in webapp/app.py.
from webapp.app import app  # noqa: E402

__all__ = ["app"]

if __name__ == "__main__":
    # Convenience: `python wsgi.py` for a quick local smoke test.
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
