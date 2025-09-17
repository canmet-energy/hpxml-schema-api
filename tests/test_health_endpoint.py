from pathlib import Path

from fastapi.testclient import TestClient

from hpxml_schema_api.app import RulesRepository, app, get_repository

FIXTURE_RULES = (
    Path(__file__).resolve().parent / "fixtures" / "schema" / "sample_rules.json"
)


def override_repository():
    return RulesRepository(FIXTURE_RULES)


def test_health_basic():
    """Health endpoint should return healthy status and include schema version key."""
    app.dependency_overrides[get_repository] = override_repository
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "schema_version" in data
