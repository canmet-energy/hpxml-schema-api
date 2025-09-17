import copy
import json
from pathlib import Path

from hpxml_schema_api.app import RulesRepository

FIXTURE_RULES = (
    Path(__file__).resolve().parent / "fixtures" / "schema" / "sample_rules.json"
)


def load_fixture():
    return json.loads(FIXTURE_RULES.read_text())


def test_version_matrix_simulated(monkeypatch):
    base = load_fixture()
    current_version = {"value": "4.0"}

    class _FakeNode:
        def __init__(self, name, xpath, kind, children):
            self.name = name
            self.xpath = xpath
            self.kind = kind
            self.children = children
            self.enum_values = []
            self.validations = []
            self.min_occurs = 0
            self.data_type = None

        @classmethod
        def from_dict(cls, data):
            children = [cls.from_dict(c) for c in data.get("children", [])]
            return cls(data["name"], data["xpath"], data["kind"], children)

    def fake_init(self, mode="cached", rules_path=None, parser_config=None):
        self.mode = "cached"
        self.parser_config = parser_config or type("Cfg", (), {})()
        variant_root = copy.deepcopy(base["root"])
        self.root = _FakeNode.from_dict(variant_root)
        self.metadata = {
            "schema_version": current_version["value"],
            "source": "fixture",
            "generated_at": "now",
            "parser_mode": "cached",
            "parser_config": {},
        }
        self.etag = '"dummy"'
        from datetime import datetime

        self.last_modified = datetime.now()

    monkeypatch.setattr("hpxml_schema_api.app.RulesRepository.__init__", fake_init)

    repo_a = RulesRepository()
    assert repo_a.metadata["schema_version"] == "4.0"

    current_version["value"] = "4.1"
    repo_b = RulesRepository()
    assert repo_b.metadata["schema_version"] == "4.1"
