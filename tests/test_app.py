from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hpxml_schema_api.app import (
    app,
    get_repository,
    RulesRepository,
    ValidationRequest,
    ValidationResponse,
)

FIXTURE_RULES = Path(__file__).resolve().parent / "fixtures" / "schema" / "sample_rules.json"


def override_repository():
    return RulesRepository(FIXTURE_RULES)


def create_client():
    app.dependency_overrides[get_repository] = override_repository
    return TestClient(app)


def test_health_endpoint():
    client = create_client()
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "schema_version" in data


def test_tree_returns_section():
    client = create_client()
    response = client.get(
        "/tree",
        params={
            "section": "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall",
        },
    )
    assert response.status_code == 200
    data = response.json()["node"]
    assert data["name"] == "Wall"
    assert data["repeatable"] is True


def test_fields_endpoint_lists_children():
    client = create_client()
    response = client.get(
        "/fields",
        params={
            "section": "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall",
        },
    )
    assert response.status_code == 200
    body = response.json()
    field_names = {field["name"] for field in body["fields"]}
    assert "WallArea" in field_names
    assert "ExteriorAdjacentTo" in field_names


def test_search_endpoint():
    client = create_client()
    response = client.get("/search", params={"query": "roof"})
    assert response.status_code == 200
    results = response.json()["results"]
    assert any("Roof" in item["name"] for item in results)


def test_metadata_endpoint():
    client = create_client()
    response = client.get("/metadata")
    assert response.status_code == 200
    data = response.json()
    assert "schema_version" in data
    assert "source" in data
    # Check cache headers
    assert "ETag" in response.headers
    assert "Cache-Control" in response.headers
    assert "Last-Modified" in response.headers


def test_metadata_endpoint_with_etag():
    client = create_client()
    # First request
    response1 = client.get("/metadata")
    assert response1.status_code == 200
    etag = response1.headers["ETag"]

    # Second request with If-None-Match
    response2 = client.get("/metadata", headers={"If-None-Match": etag})
    assert response2.status_code == 304


def test_tree_endpoint_with_depth():
    client = create_client()
    response = client.get("/tree", params={"depth": 2})
    assert response.status_code == 200
    data = response.json()["node"]
    assert data["name"] == "HPXML"
    # Verify depth limiting works
    if data.get("children"):
        for child in data["children"]:
            if child.get("children"):
                for grandchild in child["children"]:
                    # Third level should have no children due to depth=2
                    assert not grandchild.get("children")


def test_tree_endpoint_not_found():
    client = create_client()
    response = client.get("/tree", params={"section": "/Invalid/Path"})
    assert response.status_code == 404
    assert "Section not found" in response.json()["detail"]


def test_fields_endpoint_not_found():
    client = create_client()
    response = client.get("/fields", params={"section": "/Invalid/Path"})
    assert response.status_code == 404
    assert "Section not found" in response.json()["detail"]


def test_search_endpoint_with_filters():
    client = create_client()
    # Test with kind filter
    response = client.get("/search", params={"query": "Wall", "kind": "field"})
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert "total" in data
    assert "limited" in data

    # All results should be fields
    for result in data["results"]:
        assert result["kind"] == "field"


def test_search_endpoint_with_limit():
    client = create_client()
    response = client.get("/search", params={"query": "wall", "limit": 5})
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) <= 5
    if len(data["results"]) == 5:
        assert data["limited"] is True


def test_search_endpoint_minimum_query_length():
    client = create_client()
    response = client.get("/search", params={"query": "a"})
    assert response.status_code == 422  # Validation error for min_length


def test_validate_endpoint():
    client = create_client()
    # Test valid case
    response = client.post(
        "/validate",
        json={
            "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/WallArea",
            "value": "100",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "valid" in data
    assert "errors" in data
    assert "warnings" in data


def test_validate_endpoint_unknown_xpath():
    client = create_client()
    response = client.post(
        "/validate",
        json={
            "xpath": "/Invalid/Path",
            "value": "test",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False
    assert len(data["errors"]) > 0
    assert "Unknown xpath" in data["errors"][0]


def test_schema_version_endpoint():
    client = create_client()
    response = client.get("/schema-version")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert "source" in data
    assert "generated_at" in data


def test_custom_404_handler():
    client = create_client()
    response = client.get("/nonexistent-endpoint")
    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert "path" in data
    assert data["error"] == "Not Found"


def test_openapi_documentation():
    client = create_client()
    # Test OpenAPI schema endpoint
    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    assert data["info"]["title"] == "HPXML Rules API"
    assert data["info"]["version"] == "0.3.0"
    assert "paths" in data
    assert "/health" in data["paths"]
    assert "/validate" in data["paths"]


def test_docs_endpoint():
    client = create_client()
    response = client.get("/docs")
    assert response.status_code == 200
    assert b"swagger-ui" in response.content


def test_redoc_endpoint():
    client = create_client()
    response = client.get("/redoc")
    assert response.status_code == 200
    assert b"redoc" in response.content


@pytest.fixture(autouse=True)
def cleanup_overrides():
    yield
    app.dependency_overrides.clear()
