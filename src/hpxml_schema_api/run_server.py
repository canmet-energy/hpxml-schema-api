"""Executable entry point for launching the HPXML Rules FastAPI application.

This module is intentionally minimal so that process managers (uvicorn / gunicorn /
ASGI workers) can import a stable `app` object from `hpxml_schema_api.app` OR run
`python -m hpxml_schema_api.run_server` directly for local development.

Environment Variables:
    PORT (int): Override listening port (default 8000).

Example:
    $ python -m hpxml_schema_api.run_server
    $ PORT=9000 python -m hpxml_schema_api.run_server

Production Recommendation:
    Prefer invoking uvicorn or another ASGI server directly for tuned concurrency:
        uvicorn hpxml_schema_api.app:app --host 0.0.0.0 --port 8000 --workers 4
"""

from __future__ import annotations

import os

import uvicorn

from .app import app


def main() -> None:
    """Launch the ASGI server with development-friendly defaults.

    Reads the ``PORT`` environment variable (default 8000). For production,
    start uvicorn or gunicorn explicitly so you can configure workers, reload
    behavior, and logging more granularly.
    """
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
