"""Tests for /versions endpoint and latest alias behavior."""

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hpxml_schema_api.models import RuleNode
from hpxml_schema_api.version_manager import SchemaVersionInfo
from hpxml_schema_api.versioned_routes import create_versioned_router


def _build_mock_manager():
    manager = MagicMock()
    manager.get_available_versions.return_value = ["4.1", "4.0"]  # newest first
    manager.get_default_version.return_value = "4.0"
    manager.validate_version.side_effect = lambda v: v in ["4.0", "4.1"]

    v40 = SchemaVersionInfo(
        version="4.0",
        path=None,  # Not needed for these tests
        description="HPXML Schema v4.0",
        default=True,
        release_date="2024-01-01",
    )
    v41 = SchemaVersionInfo(
        version="4.1",
        path=None,
        description="HPXML Schema v4.1",
        default=False,
        release_date="2024-06-01",
    )
    manager.get_version_info.side_effect = lambda v: {"4.0": v40, "4.1": v41}.get(v)
    return manager


def _build_mock_parser():
    parser = MagicMock()
    # Minimal tree for counting
    parser.parse_xsd.return_value = RuleNode(
        xpath="/HPXML", name="HPXML", kind="section"
    )
    return parser


def _make_client(mock_manager):
    app = FastAPI()
    app.include_router(create_versioned_router())
    return TestClient(app)


@patch("hpxml_schema_api.versioned_routes.get_version_manager")
def test_versions_listing(mock_get_manager):
    mock_manager = _build_mock_manager()
    mock_get_manager.return_value = mock_manager
    client = _make_client(mock_manager)

    resp = client.get("/versions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["default_version"] == "4.0"
    assert {v["version"] for v in data["versions"]} == {"4.0", "4.1"}
    # Ensure endpoints included
    v40 = next(v for v in data["versions"] if v["version"] == "4.0")
    assert v40["endpoints"]["metadata"] == "/v4.0/metadata"


@patch("hpxml_schema_api.versioned_routes.get_version_manager")
@patch("hpxml_schema_api.versioned_routes.get_versioned_parser")
def test_latest_alias_metadata(mock_get_parser, mock_get_manager):
    mock_manager = _build_mock_manager()
    mock_get_manager.return_value = mock_manager
    mock_parser = _build_mock_parser()
    mock_get_parser.return_value = mock_parser
    client = _make_client(mock_manager)

    resp = client.get("/vlatest/metadata")
    assert resp.status_code == 200
    data = resp.json()
    # latest should map to 4.1 (highest)
    assert data["version"] == "4.1"
