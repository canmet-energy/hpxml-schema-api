from pathlib import Path

from hpxml_schema_api.merger import build_rules_tree

FIXTURE_XSD = Path(__file__).resolve().parent / "fixtures" / "schema" / "sample_hpxml.xsd"
FIXTURE_SCH = Path(__file__).resolve().parent / "fixtures" / "schema" / "sample_schematron.xml"


def test_build_rules_tree_combines_sources():
    root = build_rules_tree(FIXTURE_XSD, FIXTURE_SCH)
    wall_xpath = "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall"
    wall = _find(root, wall_xpath)
    assert wall is not None
    assert any(rule.message.startswith("Wall must") for rule in wall.validations)


def _find(node, xpath):
    if node.xpath == xpath:
        return node
    for child in node.children:
        match = _find(child, xpath)
        if match is not None:
            return match
    return None
