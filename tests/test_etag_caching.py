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


def test_metadata_etag_and_conditional():
    first = client.get("/metadata")
    assert first.status_code == 200
    etag = first.headers.get("ETag")
    assert etag
    # Conditional request should yield 304 and no (or empty) body
    second = client.get("/metadata", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.text in ("", "{}")


def test_tree_etag_and_conditional_root():
    first = client.get("/tree")
    assert first.status_code == 200
    etag = first.headers.get("ETag")
    assert etag
    second = client.get("/tree", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.text in ("", "{}")
