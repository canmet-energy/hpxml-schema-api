#!/usr/bin/env python
"""Export OpenAPI and GraphQL schema artifacts.

Usage:
    python scripts/export_schemas.py --out-dir build/schemas

Outputs:
    openapi.json              FastAPI OpenAPI spec
    graphql_schema.graphql    GraphQL SDL
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hpxml_schema_api.app import app
from hpxml_schema_api.graphql_schema import schema as graphql_schema


def export_openapi(out_dir: Path) -> Path:
    spec = app.openapi()
    path = out_dir / "openapi.json"
    path.write_text(json.dumps(spec, indent=2))
    return path


def export_graphql(out_dir: Path) -> Path:
    sdl = graphql_schema.as_str()
    path = out_dir / "graphql_schema.graphql"
    path.write_text(sdl)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="build/schemas", help="Output directory")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    openapi_path = export_openapi(out_dir)
    graphql_path = export_graphql(out_dir)

    print(f"Exported OpenAPI -> {openapi_path}")
    print(f"Exported GraphQL SDL -> {graphql_path}")


if __name__ == "__main__":  # pragma: no cover
    main()
