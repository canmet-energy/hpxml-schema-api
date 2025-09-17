"""Tests for version management functionality."""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from hpxml_schema_api.version_manager import (
    VersionManager,
    SchemaVersionInfo,
    get_version_manager,
    get_versioned_parser
)
from hpxml_schema_api.xsd_parser import ParserConfig


class TestSchemaVersionInfo:
    """Test SchemaVersionInfo dataclass."""

    def test_create_version_info(self):
        """Test creating version info."""
        version_info = SchemaVersionInfo(
            version="4.0",
            path=Path("/test/HPXML.xsd"),
            description="Test version",
            release_date="2024-01-01",
            deprecated=False,
            default=True
        )

        assert version_info.version == "4.0"
        assert version_info.path == Path("/test/HPXML.xsd")
        assert version_info.description == "Test version"
        assert version_info.release_date == "2024-01-01"
        assert version_info.deprecated is False
        assert version_info.default is True

    def test_default_values(self):
        """Test default values for optional fields."""
        version_info = SchemaVersionInfo(
            version="4.1",
            path=Path("/test/HPXML-4.1.xsd"),
            description="Test version 4.1"
        )

        assert version_info.release_date is None
        assert version_info.deprecated is False
        assert version_info.default is False


class TestVersionManager:
    """Test VersionManager class."""

    def setup_method(self):
        """Set up test environment."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.manager = VersionManager(schema_dir=self.temp_dir)

    def teardown_method(self):
        """Clean up test environment."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_init_with_empty_directory(self):
        """Test initialization with empty directory."""
        assert len(self.manager.versions) == 0
        assert self.manager.schema_dir == self.temp_dir

    def test_load_versioned_subdirectories(self):
        """Test loading schemas from versioned subdirectories."""
        # Create version subdirectories
        v40_dir = self.temp_dir / "4.0"
        v41_dir = self.temp_dir / "4.1"
        v40_dir.mkdir()
        v41_dir.mkdir()

        # Create schema files
        (v40_dir / "HPXML.xsd").write_text(self._create_minimal_xsd())
        (v41_dir / "HPXML.xsd").write_text(self._create_minimal_xsd())

        # Reload version catalog
        self.manager._load_version_catalog()

        assert "4.0" in self.manager.versions
        assert "4.1" in self.manager.versions
        assert self.manager.versions["4.0"].path == v40_dir / "HPXML.xsd"
        assert self.manager.versions["4.1"].path == v41_dir / "HPXML.xsd"

    def test_load_versioned_files(self):
        """Test loading schemas from versioned files."""
        # Create versioned schema files
        (self.temp_dir / "HPXML-4.0.xsd").write_text(self._create_minimal_xsd())
        (self.temp_dir / "HPXML-4.1.xsd").write_text(self._create_minimal_xsd())

        # Reload version catalog
        self.manager._load_version_catalog()

        assert "4.0" in self.manager.versions
        assert "4.1" in self.manager.versions
        assert self.manager.versions["4.0"].path == self.temp_dir / "HPXML-4.0.xsd"
        assert self.manager.versions["4.1"].path == self.temp_dir / "HPXML-4.1.xsd"

    def test_load_single_schema(self):
        """Test loading single HPXML.xsd file."""
        # Create single schema file
        (self.temp_dir / "HPXML.xsd").write_text(self._create_minimal_xsd())

        # Reload version catalog
        self.manager._load_version_catalog()

        assert "4.0" in self.manager.versions
        assert self.manager.versions["4.0"].path == self.temp_dir / "HPXML.xsd"
        assert self.manager.versions["4.0"].default is True

    def test_get_available_versions(self):
        """Test getting available versions."""
        # Add some versions
        self.manager.versions = {
            "4.0": SchemaVersionInfo("4.0", Path("/test"), "Test 4.0"),
            "4.1": SchemaVersionInfo("4.1", Path("/test"), "Test 4.1"),
            "3.9": SchemaVersionInfo("3.9", Path("/test"), "Test 3.9")
        }

        versions = self.manager.get_available_versions()
        assert versions == ["4.1", "4.0", "3.9"]  # Sorted descending

    def test_get_default_version(self):
        """Test getting default version."""
        # No versions
        assert self.manager.get_default_version() is None

        # Add versions with explicit default
        self.manager.versions = {
            "4.0": SchemaVersionInfo("4.0", Path("/test"), "Test 4.0", default=True),
            "4.1": SchemaVersionInfo("4.1", Path("/test"), "Test 4.1")
        }
        assert self.manager.get_default_version() == "4.0"

        # No explicit default - should return latest
        self.manager.versions = {
            "4.0": SchemaVersionInfo("4.0", Path("/test"), "Test 4.0"),
            "4.1": SchemaVersionInfo("4.1", Path("/test"), "Test 4.1")
        }
        assert self.manager.get_default_version() == "4.1"

    def test_is_version_available(self):
        """Test checking version availability."""
        self.manager.versions = {
            "4.0": SchemaVersionInfo("4.0", Path("/test"), "Test 4.0")
        }

        assert self.manager.is_version_available("4.0") is True
        assert self.manager.is_version_available("4.1") is False
        assert self.manager.is_version_available("") is False

    def test_validate_version(self):
        """Test version validation."""
        self.manager.versions = {
            "4.0": SchemaVersionInfo("4.0", Path("/test"), "Test 4.0")
        }

        assert self.manager.validate_version("4.0") is True
        assert self.manager.validate_version("4.1") is False
        assert self.manager.validate_version("") is False
        assert self.manager.validate_version("invalid") is False

    def test_get_compatible_versions(self):
        """Test getting compatible versions."""
        self.manager.versions = {
            "3.9": SchemaVersionInfo("3.9", Path("/test"), "Test 3.9"),
            "4.0": SchemaVersionInfo("4.0", Path("/test"), "Test 4.0"),
            "4.1": SchemaVersionInfo("4.1", Path("/test"), "Test 4.1"),
            "4.2": SchemaVersionInfo("4.2", Path("/test"), "Test 4.2")
        }

        compatible = self.manager.get_compatible_versions("4.0")
        assert compatible == ["4.0", "4.1", "4.2"]

        compatible = self.manager.get_compatible_versions("4.1")
        assert compatible == ["4.1", "4.2"]

        compatible = self.manager.get_compatible_versions("5.0")
        assert compatible == []

    @patch('hpxml_schema_api.version_manager._get_default_cache')
    def test_get_parser(self, mock_get_cache):
        """Test getting parser for version."""
        mock_cache = MagicMock()
        mock_get_cache.return_value = mock_cache

        # Create test schema file
        schema_file = self.temp_dir / "HPXML.xsd"
        schema_file.write_text(self._create_minimal_xsd())

        self.manager.versions = {
            "4.0": SchemaVersionInfo("4.0", schema_file, "Test 4.0")
        }

        config = ParserConfig()
        parser = self.manager.get_parser("4.0", config)

        assert parser is not None
        assert parser.schema_path == schema_file

        # Test caching
        parser2 = self.manager.get_parser("4.0", config)
        assert parser is parser2

    def test_get_parser_invalid_version(self):
        """Test getting parser for invalid version."""
        parser = self.manager.get_parser("invalid")
        assert parser is None

    def test_clear_parser_cache(self):
        """Test clearing parser cache."""
        # Add some parsers to cache
        self.manager.parsers = {
            "4.0:config1": MagicMock(),
            "4.0:config2": MagicMock(),
            "4.1:config1": MagicMock()
        }

        # Clear specific version
        self.manager.clear_parser_cache("4.0")
        assert "4.1:config1" in self.manager.parsers
        assert len(self.manager.parsers) == 1

        # Clear all
        self.manager.clear_parser_cache()
        assert len(self.manager.parsers) == 0

    @patch.dict('os.environ', {'HPXML_SCHEMA_DIR': '/custom/schema/dir'})
    @patch('pathlib.Path.exists')
    @patch('pathlib.Path.is_dir')
    def test_discover_schema_directory_env_var(self, mock_is_dir, mock_exists):
        """Test discovering schema directory from environment variable."""
        mock_exists.return_value = True
        mock_is_dir.return_value = True

        manager = VersionManager()
        assert manager.schema_dir == Path('/custom/schema/dir')

    def test_discovery_fallback_to_single_version(self):
        """Test fallback to single version discovery."""
        # Create a mock for the single version discovery
        with patch.object(self.manager, '_load_single_version') as mock_load_single:
            # Set schema_dir to None to trigger fallback
            self.manager.schema_dir = None
            self.manager._load_version_catalog()
            mock_load_single.assert_called_once()

    def _create_minimal_xsd(self) -> str:
        """Create minimal XSD content for testing."""
        return '''<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           targetNamespace="http://hpxmlonline.com/2019/10"
           elementFormDefault="qualified">
    <xs:element name="HPXML">
        <xs:complexType>
            <xs:sequence>
                <xs:element name="TestElement" type="xs:string"/>
            </xs:sequence>
        </xs:complexType>
    </xs:element>
</xs:schema>'''


class TestGlobalFunctions:
    """Test global version manager functions."""

    def test_get_version_manager_singleton(self):
        """Test that get_version_manager returns singleton."""
        manager1 = get_version_manager()
        manager2 = get_version_manager()
        assert manager1 is manager2

    @patch('hpxml_schema_api.version_manager.get_version_manager')
    def test_get_versioned_parser(self, mock_get_manager):
        """Test get_versioned_parser function."""
        mock_manager = MagicMock()
        mock_parser = MagicMock()
        mock_manager.get_default_version.return_value = "4.0"
        mock_manager.get_parser.return_value = mock_parser
        mock_get_manager.return_value = mock_manager

        # Test with explicit version
        parser = get_versioned_parser("4.1", ParserConfig())
        mock_manager.get_parser.assert_called_with("4.1", ParserConfig())
        assert parser is mock_parser

        # Test with default version
        parser = get_versioned_parser(None, ParserConfig())
        mock_manager.get_default_version.assert_called_once()
        mock_manager.get_parser.assert_called_with("4.0", ParserConfig())

    @patch('hpxml_schema_api.version_manager.get_version_manager')
    def test_get_versioned_parser_no_default(self, mock_get_manager):
        """Test get_versioned_parser when no default version available."""
        mock_manager = MagicMock()
        mock_manager.get_default_version.return_value = None
        mock_get_manager.return_value = mock_manager

        parser = get_versioned_parser(None)
        assert parser is None


class TestVersionManagerIntegration:
    """Integration tests for version manager."""

    def setup_method(self):
        """Set up integration test environment."""
        self.temp_dir = Path(tempfile.mkdtemp())

    def teardown_method(self):
        """Clean up integration test environment."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_full_workflow(self):
        """Test complete version management workflow."""
        # Create multiple schema versions
        v40_dir = self.temp_dir / "4.0"
        v41_dir = self.temp_dir / "4.1"
        v40_dir.mkdir()
        v41_dir.mkdir()

        v40_schema = self._create_schema_content("4.0")
        v41_schema = self._create_schema_content("4.1")

        (v40_dir / "HPXML.xsd").write_text(v40_schema)
        (v41_dir / "HPXML.xsd").write_text(v41_schema)

        # Initialize manager
        manager = VersionManager(schema_dir=self.temp_dir)

        # Test discovery
        versions = manager.get_available_versions()
        assert "4.0" in versions
        assert "4.1" in versions

        # Test version info
        v40_info = manager.get_version_info("4.0")
        assert v40_info is not None
        assert v40_info.version == "4.0"
        assert v40_info.path.exists()

        # Test parser creation
        parser = manager.get_parser("4.0")
        assert parser is not None
        assert parser.schema_path == v40_info.path

        # Test validation
        assert manager.validate_version("4.0") is True
        assert manager.validate_version("nonexistent") is False

    def _create_schema_content(self, version: str) -> str:
        """Create schema content for specific version."""
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           targetNamespace="http://hpxmlonline.com/2019/10"
           elementFormDefault="qualified"
           version="{version}">
    <xs:element name="HPXML">
        <xs:complexType>
            <xs:sequence>
                <xs:element name="Version" type="xs:string" fixed="{version}"/>
                <xs:element name="Building" minOccurs="0" maxOccurs="unbounded">
                    <xs:complexType>
                        <xs:sequence>
                            <xs:element name="BuildingID" type="xs:string"/>
                        </xs:sequence>
                    </xs:complexType>
                </xs:element>
            </xs:sequence>
        </xs:complexType>
    </xs:element>
</xs:schema>'''