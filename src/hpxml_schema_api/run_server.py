"""Entry point to launch the HPXML rules API."""

from __future__ import annotations

import os

import uvicorn

from .app import app


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
