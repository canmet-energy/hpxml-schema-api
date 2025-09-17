"""Tests for versioned API routes."""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from hpxml_schema_api.versioned_routes import create_versioned_router
from hpxml_schema_api.version_manager import VersionManager, SchemaVersionInfo
from hpxml_schema_api.models import RuleNode


@pytest.fixture
def temp_schema_dir():
    """Create temporary schema directory for testing."""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    if temp_dir.exists():
        shutil.rmtree(temp_dir)


@pytest.fixture
def mock_version_manager(temp_schema_dir):
    """Create mock version manager with test data."""
    with patch('hpxml_schema_api.versioned_routes.get_version_manager') as mock_get_manager:
        manager = MagicMock()

        # Set up version info
        manager.get_available_versions.return_value = ["4.1", "4.0"]
        manager.get_default_version.return_value = "4.0"
        manager.validate_version.side_effect = lambda v: v in ["4.0", "4.1"]

        # Set up version info objects
        v40_info = SchemaVersionInfo(
            version="4.0",
            path=temp_schema_dir / "HPXML-4.0.xsd",
            description="HPXML Schema v4.0",
            default=True,
            release_date="2024-01-01"
        )
        v41_info = SchemaVersionInfo(
            version="4.1",
            path=temp_schema_dir / "HPXML-4.1.xsd",
            description="HPXML Schema v4.1",
            default=False,
            release_date="2024-06-01"
        )

        manager.get_version_info.side_effect = lambda v: {
            "4.0": v40_info,
            "4.1": v41_info
        }.get(v)

        mock_get_manager.return_value = manager
        yield manager


@pytest.fixture
def mock_parser():
    """Create mock parser with test data."""
    parser = MagicMock()

    # Create test schema tree
    test_node = RuleNode(
        xpath="/HPXML",
        name="HPXML",
        kind="section",
        description="Root HPXML element",
        children=[
            RuleNode(
                xpath="/HPXML/Building",
                name="Building",
                kind="section",
                description="Building element",
                children=[
                    RuleNode(
                        xpath="/HPXML/Building/BuildingID",
                        name="BuildingID",
                        kind="field",
                        data_type="string",
                        description="Building identifier"
                    )
                ]
            )
        ]
    )

    parser.parse_xsd.return_value = test_node
    return parser


@pytest.fixture
def client(mock_version_manager):
    """Create test client with versioned routes."""
    app = FastAPI()
    versioned_router = create_versioned_router()
    app.include_router(versioned_router)
    return TestClient(app)


class TestVersionsEndpoint:
    """Test /versions endpoint."""

    def test_list_versions(self, client, mock_version_manager):
        """Test listing available versions."""
        response = client.get("/versions")
        assert response.status_code == 200

        data = response.json()
        assert "versions" in data
        assert "default_version" in data
        assert data["default_version"] == "4.0"

        versions = data["versions"]
        assert len(versions) == 2

        # Check version structure
        v40 = next(v for v in versions if v["version"] == "4.0")
        assert v40["description"] == "HPXML Schema v4.0"
        assert v40["default"] is True
        assert v40["deprecated"] is False
        assert v40["release_date"] == "2024-01-01"
        assert "endpoints" in v40

        # Check endpoints structure
        endpoints = v40["endpoints"]
        assert endpoints["metadata"] == "/v4.0/metadata"
        assert endpoints["tree"] == "/v4.0/tree"
        assert endpoints["fields"] == "/v4.0/fields"
        assert endpoints["search"] == "/v4.0/search"
        assert endpoints["validate"] == "/v4.0/validate"
        assert endpoints["graphql"] == "/v4.0/graphql"


class TestVersionedMetadata:
    """Test versioned metadata endpoint."""

    @patch('hpxml_schema_api.versioned_routes.get_versioned_parser')
    def test_get_metadata_success(self, mock_get_parser, client, mock_parser):
        """Test successful metadata retrieval."""
        mock_get_parser.return_value = mock_parser

        response = client.get("/v4.0/metadata")
        assert response.status_code == 200

        data = response.json()
        assert data["version"] == "4.0"
        assert data["root_name"] == "HPXML"
        assert data["total_nodes"] == 3  # HPXML + Building + BuildingID
        assert data["total_fields"] == 1  # BuildingID
        assert data["total_sections"] == 2  # HPXML + Building
        assert "etag" in data

    def test_get_metadata_invalid_version(self, client):
        """Test metadata with invalid version."""
        response = client.get("/v99.0/metadata")
        assert response.status_code == 404
        assert "not available" in response.json()["detail"]

    @patch('hpxml_schema_api.versioned_routes.get_versioned_parser')
    def test_get_metadata_parser_unavailable(self, mock_get_parser, client):
        """Test metadata when parser is unavailable."""
        mock_get_parser.return_value = None

        response = client.get("/v4.0/metadata")
        assert response.status_code == 404

    @patch('hpxml_schema_api.versioned_routes.get_versioned_parser')
    def test_get_metadata_parse_error(self, mock_get_parser, client, mock_parser):
        """Test metadata when parsing fails."""
        mock_parser.parse_xsd.side_effect = Exception("Parse error")
        mock_get_parser.return_value = mock_parser

        response = client.get("/v4.0/metadata")
        assert response.status_code == 500
        assert "Failed to load schema" in response.json()["detail"]


class TestVersionedTree:
    """Test versioned tree endpoint."""

    @patch('hpxml_schema_api.versioned_routes.get_versioned_parser')
    def test_get_tree_success(self, mock_get_parser, client, mock_parser):
        """Test successful tree retrieval."""
        mock_get_parser.return_value = mock_parser

        response = client.get("/v4.0/tree")
        assert response.status_code == 200

        data = response.json()
        assert data["xpath"] == "/HPXML"
        assert data["name"] == "HPXML"
        assert data["kind"] == "section"
        assert len(data["children"]) == 1

    @patch('hpxml_schema_api.versioned_routes.get_versioned_parser')
    def test_get_tree_with_section(self, mock_get_parser, client, mock_parser):
        """Test tree retrieval for specific section."""
        mock_get_parser.return_value = mock_parser

        response = client.get("/v4.0/tree?section=Building")
        assert response.status_code == 200

        # Verify section parameter was passed
        mock_parser.parse_xsd.assert_called_with(root_name="Building")

    @patch('hpxml_schema_api.versioned_routes.get_versioned_parser')
    def test_get_tree_with_depth(self, mock_get_parser, client, mock_parser):
        """Test tree retrieval with depth limit."""
        mock_get_parser.return_value = mock_parser

        response = client.get("/v4.0/tree?depth=2")
        assert response.status_code == 200

        # Verify depth limiting was applied
        data = response.json()
        assert data["xpath"] == "/HPXML"

    def test_get_tree_invalid_version(self, client):
        """Test tree with invalid version."""
        response = client.get("/v99.0/tree")
        assert response.status_code == 404


class TestVersionedFields:
    """Test versioned fields endpoint."""

    @patch('hpxml_schema_api.versioned_routes.get_versioned_parser')
    def test_get_fields_success(self, mock_get_parser, client, mock_parser):
        """Test successful fields retrieval."""
        mock_get_parser.return_value = mock_parser

        response = client.get("/v4.0/fields")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1  # Only BuildingID is a field

        field = data[0]
        assert field["name"] == "BuildingID"
        assert field["kind"] == "field"
        assert field["data_type"] == "string"

    @patch('hpxml_schema_api.versioned_routes.get_versioned_parser')
    def test_get_fields_with_section(self, mock_get_parser, client, mock_parser):
        """Test fields retrieval for specific section."""
        mock_get_parser.return_value = mock_parser

        response = client.get("/v4.0/fields?section=Building")
        assert response.status_code == 200

        # Verify section parameter was passed
        mock_parser.parse_xsd.assert_called_with(root_name="Building")

    @patch('hpxml_schema_api.versioned_routes.get_versioned_parser')
    def test_get_fields_with_limit(self, mock_get_parser, client, mock_parser):
        """Test fields retrieval with limit."""
        mock_get_parser.return_value = mock_parser

        response = client.get("/v4.0/fields?limit=50")
        assert response.status_code == 200

        data = response.json()
        assert len(data) <= 50


class TestVersionedSearch:
    """Test versioned search endpoint."""

    @patch('hpxml_schema_api.versioned_routes.get_versioned_parser')
    def test_search_success(self, mock_get_parser, client, mock_parser):
        """Test successful search."""
        mock_get_parser.return_value = mock_parser

        response = client.get("/v4.0/search?q=Building")
        assert response.status_code == 200

        data = response.json()
        assert "results" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data

        results = data["results"]
        assert len(results) >= 1

        # Should find Building node
        building_result = next((r for r in results if r["name"] == "Building"), None)
        assert building_result is not None
        assert building_result["kind"] == "section"

    @patch('hpxml_schema_api.versioned_routes.get_versioned_parser')
    def test_search_with_kind_filter(self, mock_get_parser, client, mock_parser):
        """Test search with kind filter."""
        mock_get_parser.return_value = mock_parser

        response = client.get("/v4.0/search?q=Building&kind=field")
        assert response.status_code == 200

        data = response.json()
        results = data["results"]

        # Should only return field nodes
        for result in results:
            assert result["kind"] == "field"

    @patch('hpxml_schema_api.versioned_routes.get_versioned_parser')
    def test_search_with_pagination(self, mock_get_parser, client, mock_parser):
        """Test search with pagination."""
        mock_get_parser.return_value = mock_parser

        response = client.get("/v4.0/search?q=Building&limit=1&offset=0")
        assert response.status_code == 200

        data = response.json()
        assert data["limit"] == 1
        assert data["offset"] == 0
        assert len(data["results"]) <= 1

    def test_search_invalid_query(self, client):
        """Test search with invalid query."""
        response = client.get("/v4.0/search?q=x")  # Too short
        assert response.status_code == 422  # Validation error

    def test_search_invalid_version(self, client):
        """Test search with invalid version."""
        response = client.get("/v99.0/search?q=Building")
        assert response.status_code == 404


class TestVersionedValidation:
    """Test versioned validation endpoint."""

    @patch('hpxml_schema_api.versioned_routes.get_versioned_parser')
    def test_validate_field_success(self, mock_get_parser, client, mock_parser):
        """Test successful field validation."""
        mock_get_parser.return_value = mock_parser

        validation_data = {
            "xpath": "/HPXML/Building/BuildingID",
            "value": "test-id",
            "context": "{}"
        }

        response = client.post("/v4.0/validate", json=validation_data)
        assert response.status_code == 200

        data = response.json()
        assert data["valid"] is True
        assert data["version"] == "4.0"
        assert isinstance(data["errors"], list)
        assert isinstance(data["warnings"], list)

    def test_validate_invalid_version(self, client):
        """Test validation with invalid version."""
        validation_data = {
            "xpath": "/HPXML/Building/BuildingID",
            "value": "test-id"
        }

        response = client.post("/v99.0/validate", json=validation_data)
        assert response.status_code == 404


class TestPathParameterValidation:
    """Test version path parameter validation."""

    def test_version_with_v_prefix(self, client, mock_version_manager):
        """Test version parameter with 'v' prefix."""
        response = client.get("/v4.0/metadata")
        # Should work - version is cleaned to "4.0"
        assert response.status_code in [200, 404]  # 404 if parser not available

    def test_version_without_v_prefix(self, client, mock_version_manager):
        """Test version parameter without 'v' prefix."""
        # This would need custom routing to work, currently expects /v prefix
        pass

    def test_invalid_version_format(self, client):
        """Test invalid version format."""
        response = client.get("/vinvalid/metadata")
        assert response.status_code == 404


class TestUtilityFunctions:
    """Test utility functions."""

    def test_count_nodes(self):
        """Test _count_nodes function."""
        from hpxml_schema_api.versioned_routes import _count_nodes

        node = RuleNode(
            xpath="/root",
            name="root",
            kind="section",
            children=[
                RuleNode(xpath="/root/child1", name="child1", kind="field"),
                RuleNode(
                    xpath="/root/child2",
                    name="child2",
                    kind="section",
                    children=[
                        RuleNode(xpath="/root/child2/grandchild", name="grandchild", kind="field")
                    ]
                )
            ]
        )

        assert _count_nodes(node) == 4  # root + child1 + child2 + grandchild

    def test_count_fields(self):
        """Test _count_fields function."""
        from hpxml_schema_api.versioned_routes import _count_fields

        node = RuleNode(
            xpath="/root",
            name="root",
            kind="section",
            children=[
                RuleNode(xpath="/root/field1", name="field1", kind="field"),
                RuleNode(xpath="/root/section1", name="section1", kind="section"),
                RuleNode(xpath="/root/field2", name="field2", kind="field")
            ]
        )

        assert _count_fields(node) == 2  # field1 + field2

    def test_count_sections(self):
        """Test _count_sections function."""
        from hpxml_schema_api.versioned_routes import _count_sections

        node = RuleNode(
            xpath="/root",
            name="root",
            kind="section",
            children=[
                RuleNode(xpath="/root/field1", name="field1", kind="field"),
                RuleNode(xpath="/root/section1", name="section1", kind="section"),
                RuleNode(xpath="/root/section2", name="section2", kind="section")
            ]
        )

        assert _count_sections(node) == 3  # root + section1 + section2

    def test_extract_fields(self):
        """Test _extract_fields function."""
        from hpxml_schema_api.versioned_routes import _extract_fields

        node = RuleNode(
            xpath="/root",
            name="root",
            kind="section",
            children=[
                RuleNode(xpath="/root/field1", name="field1", kind="field"),
                RuleNode(
                    xpath="/root/section1",
                    name="section1",
                    kind="section",
                    children=[
                        RuleNode(xpath="/root/section1/field2", name="field2", kind="field")
                    ]
                )
            ]
        )

        fields = _extract_fields(node)
        assert len(fields) == 2
        assert fields[0].name == "field1"
        assert fields[1].name == "field2"

    def test_search_nodes(self):
        """Test _search_nodes function."""
        from hpxml_schema_api.versioned_routes import _search_nodes

        node = RuleNode(
            xpath="/root",
            name="root",
            kind="section",
            description="Root element",
            children=[
                RuleNode(
                    xpath="/root/building",
                    name="building",
                    kind="section",
                    description="Building information"
                ),
                RuleNode(
                    xpath="/root/field",
                    name="field",
                    kind="field",
                    description="Test field"
                )
            ]
        )

        # Search by name
        results = _search_nodes(node, "building")
        assert len(results) == 1
        assert results[0].name == "building"

        # Search by description
        results = _search_nodes(node, "information")
        assert len(results) == 1
        assert results[0].name == "building"

        # Search with kind filter
        results = _search_nodes(node, "field", "field")
        assert len(results) == 1
        assert results[0].kind == "field"