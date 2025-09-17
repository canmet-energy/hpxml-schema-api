from pathlib import Path

from hpxml_schema_api.schematron_parser import parse_schematron
from hpxml_schema_api.xsd_parser import parse_xsd

FIXTURE_XSD = Path(__file__).resolve().parent / "fixtures" / "schema" / "sample_hpxml.xsd"
FIXTURE_SCH = Path(__file__).resolve().parent / "fixtures" / "schema" / "sample_schematron.xml"


def _find(node, xpath):
    if node.xpath == xpath:
        return node
    for child in node.children:
        match = _find(child, xpath)
        if match is not None:
            return match
    return None


def test_attach_schematron_rules():
    root = parse_xsd(FIXTURE_XSD)
    root = parse_schematron(FIXTURE_SCH, root)

    wall_node = _find(root, "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall")
    assert wall_node is not None
    messages = [rule.message for rule in wall_node.validations]
    assert "Wall must have a SystemIdentifier." in messages
    assert any(rule.severity == "error" for rule in wall_node.validations)

    window_node = _find(
        root,
        "/HPXML/Building/BuildingDetails/Enclosure/Windows/Window",
    )
    assert window_node is not None
    severities = {rule.severity for rule in window_node.validations}
    assert "warn" in severities
    tests = {rule.test for rule in window_node.validations}
    assert "number(h:SHGC) > 0" in tests
