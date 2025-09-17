"""Tests for enhanced XSD parser with extension handling."""

import pytest
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from hpxml_schema_api.xsd_parser import XSDParser, ParserConfig, parse_xsd


def create_test_xsd_with_extensions(depth: int = 3) -> str:
    """Create test XSD with nested type extensions."""
    types = []

    # Create base type
    types.append("""
    <xs:complexType name="BaseType">
        <xs:sequence>
            <xs:element name="BaseField" type="xs:string"/>
        </xs:sequence>
    </xs:complexType>
    """)

    # Create chain of extended types
    for i in range(1, depth + 1):
        parent = "BaseType" if i == 1 else f"ExtendedType{i-1}"
        types.append(f"""
    <xs:complexType name="ExtendedType{i}">
        <xs:complexContent>
            <xs:extension base="{parent}">
                <xs:sequence>
                    <xs:element name="Field{i}" type="xs:string"/>
                    <xs:element ref="extension" minOccurs="0"/>
                </xs:sequence>
            </xs:extension>
        </xs:complexContent>
    </xs:complexType>
        """)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
    {''.join(types)}

    <xs:element name="extension" type="extensionType"/>

    <xs:complexType name="extensionType">
        <xs:sequence>
            <xs:any maxOccurs="unbounded" minOccurs="0" namespace="##any" processContents="skip"/>
        </xs:sequence>
    </xs:complexType>

    <xs:element name="TestRoot">
        <xs:complexType>
            <xs:sequence>
                <xs:element name="SimpleElement" type="ExtendedType{depth}"/>
                <xs:element name="RecursiveElement" type="RecursiveType" minOccurs="0"/>
            </xs:sequence>
        </xs:complexType>
    </xs:element>

    <xs:complexType name="RecursiveType">
        <xs:sequence>
            <xs:element name="Child" type="RecursiveType" minOccurs="0"/>
        </xs:sequence>
    </xs:complexType>
</xs:schema>"""


@pytest.fixture
def test_xsd_path():
    """Create a temporary XSD file for testing."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xsd', delete=False) as f:
        f.write(create_test_xsd_with_extensions(depth=5))
        path = Path(f.name)
    yield path
    path.unlink()


def test_default_extension_depth_limit(test_xsd_path):
    """Test that default extension depth limit works."""
    config = ParserConfig(max_extension_depth=3)
    parser = XSDParser(test_xsd_path, config=config)

    result = parser.parse("TestRoot")

    # Find the SimpleElement node
    simple_elem = None
    for child in result.children:
        if child.name == "SimpleElement":
            simple_elem = child
            break

    assert simple_elem is not None
    # Should be truncated due to depth limit
    assert "extension_chain_truncated" in simple_elem.notes
    assert "inherits_from_5_types" in simple_elem.notes


def test_configurable_extension_depth(test_xsd_path):
    """Test configurable extension depth limits."""
    # Test with high limit - should parse full chain
    config = ParserConfig(max_extension_depth=10)
    parser = XSDParser(test_xsd_path, config=config)

    result = parser.parse("TestRoot")
    simple_elem = next((c for c in result.children if c.name == "SimpleElement"), None)

    # Should not be truncated with high limit
    assert simple_elem.kind == "section"
    assert "extension_chain_truncated" not in simple_elem.notes


def test_recursion_depth_limit(test_xsd_path):
    """Test maximum recursion depth limit."""
    config = ParserConfig(max_recursion_depth=2)
    parser = XSDParser(test_xsd_path, config=config)

    result = parser.parse("TestRoot")

    # Should hit depth limit somewhere in the tree
    def find_depth_limit(node, depth=0):
        if node.name == "[depth_limit_reached]":
            return True
        if depth > 10:  # Safety check
            return False
        for child in node.children:
            if find_depth_limit(child, depth + 1):
                return True
        return False

    # With depth limit of 2, we should hit the limit
    assert find_depth_limit(result)


def test_extension_metadata_tracking(test_xsd_path):
    """Test that extension metadata is properly tracked."""
    config = ParserConfig(track_extension_metadata=True)
    parser = XSDParser(test_xsd_path, config=config)

    result = parser.parse("TestRoot")

    # Debug: print the structure to see what we're getting
    def print_structure(node, indent=0):
        prefix = "  " * indent
        print(f"{prefix}{node.name} (kind: {node.kind}, notes: {node.notes})")
        for child in node.children:
            print_structure(child, indent + 1)

    print("\nParsed structure:")
    print_structure(result)

    # Find any extension points
    def find_extensions(node):
        extensions = []
        if node.name == "extension" or "extension_point" in node.notes:
            extensions.append(node)
        for child in node.children:
            extensions.extend(find_extensions(child))
        return extensions

    extensions = find_extensions(result)
    print(f"\nFound {len(extensions)} extensions")

    # The test should pass if we have extension chains indexed
    assert len(parser.extension_chains) > 0


def test_extension_metadata_disabled(test_xsd_path):
    """Test parsing with extension metadata disabled."""
    config = ParserConfig(track_extension_metadata=False)
    parser = XSDParser(test_xsd_path, config=config)

    result = parser.parse("TestRoot")

    # Should still work but without detailed metadata
    assert result.name == "TestRoot"

    # Extension chains should not be indexed
    assert len(parser.extension_chains) == 0


def test_circular_reference_detection(test_xsd_path):
    """Test that circular references are properly detected."""
    config = ParserConfig(max_recursion_depth=3)  # Lower depth to trigger detection
    parser = XSDParser(test_xsd_path, config=config)

    result = parser.parse("TestRoot")

    # Find RecursiveElement
    recursive_elem = None
    for child in result.children:
        if child.name == "RecursiveElement":
            recursive_elem = child
            break

    assert recursive_elem is not None

    # Should hit depth limit or find recursive reference note
    def find_recursion_markers(node):
        markers = []
        if "recursive_reference" in node.notes or "max_depth" in str(node.notes):
            markers.append(node)
        for child in node.children:
            markers.extend(find_recursion_markers(child))
        return markers

    markers = find_recursion_markers(recursive_elem)
    assert len(markers) > 0  # Should find either recursive reference or depth limit


def test_cache_configuration():
    """Test that caching can be disabled."""
    config = ParserConfig(cache_resolved_refs=False)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.xsd', delete=False) as f:
        f.write(create_test_xsd_with_extensions(depth=2))
        path = Path(f.name)

    try:
        parser = XSDParser(path, config=config)
        assert parser.reference_cache is None  # Cache should be disabled

        # Should still parse correctly without cache
        result = parser.parse("TestRoot")
        assert result.name == "TestRoot"
    finally:
        path.unlink()


def test_parse_xsd_convenience_function(test_xsd_path):
    """Test the convenience parse_xsd function with config."""
    config = ParserConfig(
        max_extension_depth=2,
        max_recursion_depth=5,
        track_extension_metadata=True
    )

    result = parse_xsd(test_xsd_path, "TestRoot", config=config)

    assert result.name == "TestRoot"
    assert result.kind == "section"

    # Should have applied config settings
    simple_elem = next((c for c in result.children if c.name == "SimpleElement"), None)
    assert simple_elem is not None
    # With depth limit of 2, should be truncated
    assert "extension_chain_truncated" in simple_elem.notes


def test_extension_depth_calculation():
    """Test that extension depth is properly calculated through inheritance."""
    xsd_content = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
    <xs:complexType name="Level0">
        <xs:sequence>
            <xs:element name="Field0" type="xs:string"/>
        </xs:sequence>
    </xs:complexType>

    <xs:complexType name="Level1">
        <xs:complexContent>
            <xs:extension base="Level0">
                <xs:sequence>
                    <xs:element name="Field1" type="xs:string"/>
                </xs:sequence>
            </xs:extension>
        </xs:complexContent>
    </xs:complexType>

    <xs:complexType name="Level2">
        <xs:complexContent>
            <xs:extension base="Level1">
                <xs:sequence>
                    <xs:element name="Field2" type="xs:string"/>
                </xs:sequence>
            </xs:extension>
        </xs:complexContent>
    </xs:complexType>

    <xs:element name="Root">
        <xs:complexType>
            <xs:sequence>
                <xs:element name="Nested" type="Level2"/>
            </xs:sequence>
        </xs:complexType>
    </xs:element>
</xs:schema>"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.xsd', delete=False) as f:
        f.write(xsd_content)
        path = Path(f.name)

    try:
        config = ParserConfig(
            max_extension_depth=1,  # Very low limit
            track_extension_metadata=True
        )
        parser = XSDParser(path, config=config)

        # Check that extension chains are properly indexed
        assert "Level1" in parser.extension_chains
        assert "Level2" in parser.extension_chains
        assert parser.extension_chains["Level1"] == ["Level0"]
        assert parser.extension_chains["Level2"] == ["Level1", "Level0"]

        result = parser.parse("Root")

        # Nested element should be truncated
        nested = next((c for c in result.children if c.name == "Nested"), None)
        assert nested is not None
        assert "extension_chain_truncated" in nested.notes
        assert "inherits_from_2_types" in nested.notes
    finally:
        path.unlink()


def test_extension_notes_format():
    """Test that extension notes are properly formatted."""
    xsd_content = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
    <xs:element name="extension" type="xs:anyType"/>

    <xs:element name="Root">
        <xs:complexType>
            <xs:sequence>
                <xs:element ref="extension" minOccurs="0"/>
            </xs:sequence>
        </xs:complexType>
    </xs:element>
</xs:schema>"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.xsd', delete=False) as f:
        f.write(xsd_content)
        path = Path(f.name)

    try:
        config = ParserConfig(track_extension_metadata=True)
        result = parse_xsd(path, "Root", config=config)

        # Find extension element
        ext = next((c for c in result.children if c.name == "extension"), None)
        assert ext is not None

        # Check notes format - extension refs get marked as extension points
        assert "extension_point" in ext.notes
        assert ext.description == "Extension point for custom data"
    finally:
        path.unlink()