from pathlib import Path

from hpxml_schema_api.xsd_parser import parse_xsd

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "schema" / "sample_hpxml.xsd"


def _find(node, xpath):
    if node.xpath == xpath:
        return node
    for child in node.children:
        match = _find(child, xpath)
        if match is not None:
            return match
    return None


def test_parses_enclosure_tree():
    root = parse_xsd(FIXTURE)

    enclosure = _find(root, "/HPXML/Building/BuildingDetails/Enclosure")
    assert enclosure is not None
    assert enclosure.kind == "section"

    wall = _find(root, "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall")
    assert wall is not None
    assert wall.repeatable is True
    assert wall.kind == "section"

    identifier = _find(
        root,
        "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/SystemIdentifier",
    )
    assert identifier is not None
    assert identifier.kind == "section"
    assert _find(identifier, "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/SystemIdentifier/id")

    wall_area = _find(
        root,
        "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/WallArea",
    )
    assert wall_area is not None
    assert wall_area.kind == "field"
    assert wall_area.data_type in {"decimal", "xs:decimal"}

    enum_field = _find(
        root,
        "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/ExteriorAdjacentTo",
    )
    assert enum_field is not None
    assert enum_field.enum_values == ["outside", "attic", "garage"]

    identifiers_info = _find(
        root,
        "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/SystemIdentifiersInfo",
    )
    assert identifiers_info is not None
    assert identifiers_info.kind == "section"
    assert identifiers_info.repeatable is False
    assert _find(
        identifiers_info,
        "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/SystemIdentifiersInfo/SystemIdentifier",
    ) is not None


def test_handles_optional_roof_fields():
    root = parse_xsd(FIXTURE)
    roof = _find(root, "/HPXML/Building/BuildingDetails/Enclosure/Roofs/Roof")
    assert roof is not None
    assert roof.repeatable is True

    roof_type = _find(
        root,
        "/HPXML/Building/BuildingDetails/Enclosure/Roofs/Roof/RoofType",
    )
    assert roof_type is not None
    assert roof_type.min_occurs == 0
    assert roof_type.enum_values == ["hip", "gable", "flat"]
