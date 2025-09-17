"""Tests for schema caching functionality."""

import pytest
import tempfile
import time
from pathlib import Path

from hpxml_schema_api.cache import SchemaCache, CacheEntry, CachedSchemaParser, get_cached_parser
from hpxml_schema_api.xsd_parser import ParserConfig


def test_cache_entry_expiration():
    """Test cache entry TTL expiration."""
    entry = CacheEntry(data="test", ttl=0.1)  # 0.1 second TTL

    assert not entry.is_expired()
    time.sleep(0.2)
    assert entry.is_expired()


def test_cache_entry_staleness():
    """Test cache staleness based on file modification time."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write("test content")
        path = Path(f.name)

    try:
        # Create entry with current file time
        entry = CacheEntry(data="test", file_mtime=path.stat().st_mtime)
        assert not entry.is_stale(path)

        # Modify file
        time.sleep(0.1)
        path.write_text("modified content")
        assert entry.is_stale(path)

    finally:
        path.unlink()


def test_schema_cache_basic_operations():
    """Test basic cache operations."""
    cache = SchemaCache(default_ttl=1.0)

    # Set and get
    cache.set("test_key", "test_value")
    assert cache.get("test_key") == "test_value"

    # Non-existent key
    assert cache.get("nonexistent") is None

    # Clear
    cache.clear()
    assert cache.get("test_key") is None


def test_schema_cache_ttl():
    """Test cache TTL functionality."""
    cache = SchemaCache(default_ttl=0.1)

    cache.set("short_ttl", "value")
    assert cache.get("short_ttl") == "value"

    time.sleep(0.2)
    assert cache.get("short_ttl") is None  # Should be expired


def test_schema_cache_file_tracking():
    """Test file modification tracking."""
    cache = SchemaCache()

    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write("original content")
        path = Path(f.name)

    try:
        # Set with file tracking
        cache.set("file_key", "original_value", file_path=path)
        assert cache.get("file_key") == "original_value"

        # Check staleness
        assert not cache.check_file_staleness("file_key", path)

        # Modify file
        time.sleep(0.1)
        path.write_text("modified content")
        assert cache.check_file_staleness("file_key", path)

    finally:
        path.unlink()


def create_simple_xsd() -> str:
    """Create a simple XSD for testing."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
    <xs:element name="Root">
        <xs:complexType>
            <xs:sequence>
                <xs:element name="Field" type="xs:string"/>
            </xs:sequence>
        </xs:complexType>
    </xs:element>
</xs:schema>"""


def test_cached_schema_parser_xsd():
    """Test cached XSD parsing."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xsd', delete=False) as f:
        f.write(create_simple_xsd())
        path = Path(f.name)

    try:
        parser = CachedSchemaParser()

        # First parse - should cache
        result1 = parser.parse_xsd(path, "Root")
        assert result1.name == "Root"

        # Second parse - should use cache
        result2 = parser.parse_xsd(path, "Root")
        assert result2.name == "Root"
        assert result1.xpath == result2.xpath

        # Force refresh
        result3 = parser.parse_xsd(path, "Root", force_refresh=True)
        assert result3.name == "Root"

    finally:
        path.unlink()


def test_cached_parser_with_config():
    """Test cached parser with custom configuration."""
    config = ParserConfig(max_extension_depth=2, max_recursion_depth=5)
    parser = CachedSchemaParser(parser_config=config)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.xsd', delete=False) as f:
        f.write(create_simple_xsd())
        path = Path(f.name)

    try:
        result = parser.parse_xsd(path, "Root")
        assert result.name == "Root"

        # Verify config was applied (parser should have the config)
        assert parser.parser_config.max_extension_depth == 2
        assert parser.parser_config.max_recursion_depth == 5

    finally:
        path.unlink()


def test_cached_parser_file_modification():
    """Test that cache invalidates when file is modified."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xsd', delete=False) as f:
        f.write(create_simple_xsd())
        path = Path(f.name)

    try:
        parser = CachedSchemaParser()

        # Parse original
        result1 = parser.parse_xsd(path, "Root")
        assert result1.name == "Root"

        # Modify file
        time.sleep(0.1)
        modified_xsd = create_simple_xsd().replace("Field", "ModifiedField")
        path.write_text(modified_xsd)

        # Parse again - should detect file change and reparse
        result2 = parser.parse_xsd(path, "Root")
        assert result2.name == "Root"

        # Find the field to verify it was reparsed
        field = next((c for c in result2.children if "Field" in c.name), None)
        assert field is not None
        assert "ModifiedField" in field.name

    finally:
        path.unlink()


def test_get_cached_parser_function():
    """Test the convenience function for getting cached parsers."""
    # Default parser
    parser1 = get_cached_parser()
    parser2 = get_cached_parser()
    assert parser1 is parser2  # Should be same instance due to LRU cache

    # Parser with config
    config_key = "max_extension_depth=5,max_recursion_depth=8"
    parser3 = get_cached_parser(config_key)
    assert parser3.parser_config.max_extension_depth == 5
    assert parser3.parser_config.max_recursion_depth == 8

    # Same config should return same parser
    parser4 = get_cached_parser(config_key)
    assert parser3 is parser4


def test_cache_invalidation():
    """Test cache invalidation functionality."""
    cache = SchemaCache()

    cache.set("key1", "value1")
    cache.set("key2", "value2")

    assert cache.get("key1") == "value1"
    assert cache.get("key2") == "value2"

    # Invalidate specific key
    cache.invalidate("key1")
    assert cache.get("key1") is None
    assert cache.get("key2") == "value2"

    # Clear all
    cache.clear()
    assert cache.get("key2") is None


def test_parse_combined_caching():
    """Test caching of combined XSD results."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xsd', delete=False) as f:
        f.write(create_simple_xsd())
        xsd_path = Path(f.name)

    try:
        parser = CachedSchemaParser()

        # Parse without schematron
        result1 = parser.parse_combined(xsd_path, root_name="Root")
        assert result1.name == "Root"

        # Parse again - should use cache
        result2 = parser.parse_combined(xsd_path, root_name="Root")
        assert result1.xpath == result2.xpath

        # Force refresh
        result3 = parser.parse_combined(xsd_path, root_name="Root", force_refresh=True)
        assert result3.name == "Root"

    finally:
        xsd_path.unlink()


def test_cache_performance():
    """Test that caching provides performance benefits."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xsd', delete=False) as f:
        # Create a more complex XSD
        complex_xsd = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
    <xs:element name="Root">
        <xs:complexType>
            <xs:sequence>""" + "".join([
                f'<xs:element name="Field{i}" type="xs:string"/>'
                for i in range(50)
            ]) + """
            </xs:sequence>
        </xs:complexType>
    </xs:element>
</xs:schema>"""
        f.write(complex_xsd)
        path = Path(f.name)

    try:
        parser = CachedSchemaParser()

        # Time first parse (no cache)
        start_time = time.time()
        result1 = parser.parse_xsd(path, "Root")
        first_parse_time = time.time() - start_time

        # Time second parse (cached)
        start_time = time.time()
        result2 = parser.parse_xsd(path, "Root")
        cached_parse_time = time.time() - start_time

        # Cached should be significantly faster
        assert cached_parse_time < first_parse_time / 2
        assert result1.name == result2.name

    finally:
        path.unlink()