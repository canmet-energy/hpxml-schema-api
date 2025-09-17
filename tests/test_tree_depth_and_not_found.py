from pathlib import Path

from fastapi.testclient import TestClient

from hpxml_schema_api.app import RulesRepository, app, get_repository

FIXTURE_RULES = (
    Path(__file__).resolve().parent / "fixtures" / "schema" / "sample_rules.json"
)


def override_repository():
    return RulesRepository(FIXTURE_RULES)


app.dependency_overrides[get_repository] = override_repository
client = TestClient(app)


def test_tree_depth_limiting():
    # Apply depth limit at root to ensure pruning of grandchildren
    resp = client.get("/tree", params={"depth": 1})
    assert resp.status_code == 200
    node = resp.json()["node"]
    for child in node.get("children", []):
        # All grandchildren pruned
        assert child.get("children") == []


def test_tree_not_found():
    resp = client.get("/tree", params={"section": "/HPXML/DoesNot/Exist"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"] == "Not Found"
