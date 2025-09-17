import json
import subprocess
import sys
from pathlib import Path

from hpxml_schema_api.app import app
from hpxml_schema_api.graphql_schema import schema as graphql_schema


def test_openapi_contains_metadata_path(tmp_path):
    spec = app.openapi()
    assert "/metadata" in spec["paths"]
    assert spec["info"]["title"]


def test_graphql_schema_basic_defs():
    sdl = graphql_schema.as_str()
    assert "type Query" in sdl
    assert "type Mutation" in sdl


def test_export_script_runs(tmp_path):
    out_dir = tmp_path / "schemas"
    cmd = [sys.executable, "scripts/export_schemas.py", "--out-dir", str(out_dir)]
    subprocess.check_call(cmd)
    openapi_path = out_dir / "openapi.json"
    graphql_path = out_dir / "graphql_schema.graphql"
    assert openapi_path.exists()
    assert graphql_path.exists()
    data = json.loads(openapi_path.read_text())
    assert data.get("openapi")
