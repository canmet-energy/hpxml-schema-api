"""Version-aware schema orchestration.

The version manager centralizes discovery, caching, and retrieval of parsers
for multiple HPXML schema versions. It supports both directory-based
installations (e.g., ``schemas/4.0/HPXML.xsd``) and single-file deployments,
falling back to remote download when necessary.

Key capabilities:
* Enumerate available versions with ordering semantics (newest first)
* Provide isolated cached parsers keyed by version + parser config
* Lazy provisioning with optional remote acquisition (`download_schema`)
* Basic validation and compatibility filtering

Example:
        from hpxml_schema_api.version_manager import get_version_manager
        vm = get_version_manager()
        print("Available:", vm.get_available_versions())
        parser = vm.get_parser(vm.get_default_version())
        root = parser.parse_xsd() if parser else None

Design notes:
* Parser cache key includes the serialized config dict to allow different
    depth / extension strategies per version simultaneously.
* Integrity / signature validation of downloaded schemas is currently out of
    scope but can be layered above the downloader.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

try:
    from packaging import version
except ImportError:  # pragma: no cover - rarely triggered
    # Lightweight fallback with minimal interface (parse + InvalidVersion)
    class _FallbackVersionModule:
        @staticmethod
        def parse(v):  # naive tuple comparison
            try:
                return tuple(map(int, v.split(".")))
            except ValueError as e:  # mimic packaging semantics
                raise _FallbackVersionModule.InvalidVersion(str(e)) from e

        class InvalidVersion(Exception):
            pass

    # Expose fallback under a name matching packaging.version interface
    version = _FallbackVersionModule()  # type: ignore[assignment]

from .cache import CachedSchemaParser, _get_default_cache
from .xsd_parser import ParserConfig


@dataclass
class SchemaVersionInfo:
    """Information about a specific HPXML schema version."""

    version: str
    path: Path
    description: str
    release_date: Optional[str] = None
    deprecated: bool = False
    default: bool = False


class VersionManager:
    """Manage discovery and cached parser instances across versions."""

    def __init__(self, schema_dir: Optional[Path] = None):
        """Initialize version manager.

        Args:
            schema_dir: Directory containing versioned schema files.
                       If None, uses environment variable or discovery.
        """
        self.schema_dir = schema_dir or self._discover_schema_directory()
        self.versions: Dict[str, SchemaVersionInfo] = {}
        self.parsers: Dict[str, CachedSchemaParser] = {}
        self._load_version_catalog()

    def _discover_schema_directory(self) -> Optional[Path]:
        """Discover schema directory from environment or common locations."""
        # Check environment variable first
        env_dir = os.getenv("HPXML_SCHEMA_DIR")
        if env_dir:
            path = Path(env_dir)
            if path.exists() and path.is_dir():
                return path

        # Common locations for versioned schemas
        potential_dirs = [
            Path.cwd() / "schemas",
            Path.cwd() / "hpxml_schemas",
            Path.home() / ".local/share/hpxml-schemas",
            Path("/usr/local/share/hpxml-schemas"),
            Path("/opt/hpxml-schemas"),
        ]

        for dir_path in potential_dirs:
            if dir_path.exists() and dir_path.is_dir():
                # Check if it contains version subdirectories or files
                if any(dir_path.glob("*/HPXML.xsd")) or any(
                    dir_path.glob("HPXML-*.xsd")
                ):
                    return dir_path

        return None

    def _load_version_catalog(self) -> None:
        """Load available schema versions from directory structure."""
        if (
            not self.schema_dir
            or not self.schema_dir.exists()
            or not self.schema_dir.is_dir()
        ):
            # Fallback to single version discovery
            self._load_single_version()
            return

        try:
            # Look for versioned schemas in subdirectories (e.g., schemas/4.0/HPXML.xsd)
            for version_dir in self.schema_dir.iterdir():
                if version_dir.is_dir():
                    schema_file = version_dir / "HPXML.xsd"
                    if schema_file.exists():
                        version_str = version_dir.name
                        self.versions[version_str] = SchemaVersionInfo(
                            version=version_str,
                            path=schema_file,
                            description=f"HPXML Schema version {version_str}",
                            default=(
                                version_str == "4.0"
                            ),  # Default to 4.0 for consistency
                        )

            # Look for versioned files (e.g., HPXML-4.0.xsd, HPXML-4.1.xsd)
            for schema_file in self.schema_dir.glob("HPXML-*.xsd"):
                version_str = schema_file.stem.split("-", 1)[
                    1
                ]  # Extract version from filename
                if version_str not in self.versions:
                    self.versions[version_str] = SchemaVersionInfo(
                        version=version_str,
                        path=schema_file,
                        description=f"HPXML Schema version {version_str}",
                        default=(version_str == "4.0"),
                    )

            # If no versioned schemas found, look for single HPXML.xsd
            if not self.versions:
                schema_file = self.schema_dir / "HPXML.xsd"
                if schema_file.exists():
                    self.versions["4.0"] = SchemaVersionInfo(
                        version="4.0",
                        path=schema_file,
                        description="HPXML Schema (assumed version 4.0)",
                        default=True,
                    )
        except (FileNotFoundError, PermissionError, OSError):
            # If directory access fails, fall back to single version discovery
            self._load_single_version()

    def _load_single_version(self) -> None:
        """Load single version using existing discovery logic."""
        # Try auto-discovery with download fallback
        try:
            from .schema_downloader import auto_discover_or_download_schema

            schema_path = auto_discover_or_download_schema("4.0")
            if schema_path:
                self.versions["4.0"] = SchemaVersionInfo(
                    version="4.0",
                    path=schema_path,
                    description="HPXML Schema v4.0 (auto-discovered or downloaded)",
                    default=True,
                )
                return
        except ImportError:
            pass

        # Fallback to manual discovery
        potential_paths = [
            Path.home()
            / ".local/share/OpenStudio-HPXML-v1.9.1/HPXMLtoOpenStudio/resources/hpxml_schema/HPXML.xsd",
            Path(
                "/usr/local/openstudio/HPXMLtoOpenStudio/resources/hpxml_schema/HPXML.xsd"
            ),
            Path("/opt/openstudio/HPXMLtoOpenStudio/resources/hpxml_schema/HPXML.xsd"),
        ]

        for path in potential_paths:
            if path.exists():
                self.versions["4.0"] = SchemaVersionInfo(
                    version="4.0",
                    path=path,
                    description="HPXML Schema v4.0 (OpenStudio-HPXML)",
                    default=True,
                )
                break

    def get_available_versions(self) -> List[str]:
        """Return all known version identifiers sorted newest→oldest."""
        return sorted(
            self.versions.keys(), key=lambda v: version.parse(v), reverse=True
        )

    def get_default_version(self) -> Optional[str]:
        """Return default version (explicit flag or newest)."""
        # Look for explicitly marked default
        for ver, info in self.versions.items():
            if info.default:
                return ver

        # Fall back to latest version
        versions = self.get_available_versions()
        return versions[0] if versions else None

    def get_version_info(self, version_str: str) -> Optional[SchemaVersionInfo]:
        """Return metadata record for a version or ``None`` if absent."""
        return self.versions.get(version_str)

    def is_version_available(self, version_str: str) -> bool:
        """Return True if version has been cataloged."""
        return version_str in self.versions

    def get_parser(
        self, version_str: str, config: Optional[ParserConfig] = None
    ) -> Optional[CachedSchemaParser]:
        """Get a cached parser for a specific version.

        Args:
            version_str: Schema version to get parser for
            config: Optional parser configuration

        Returns:
            CachedSchemaParser instance or None if version not available
        """
        if version_str not in self.versions:
            return None

        # Use version-specific cache key
        cache_key = f"{version_str}:{config.__dict__ if config else 'default'}"

        if cache_key not in self.parsers:
            version_info = self.versions[version_str]
            cache = _get_default_cache()

            # Create parser with version-specific schema path
            parser = CachedSchemaParser(
                cache=cache,
                parser_config=config or ParserConfig(),
                schema_path=version_info.path,
            )

            self.parsers[cache_key] = parser

        return self.parsers[cache_key]

    def ensure_version_available(self, version_str: str) -> bool:
        """Ensure a specific version is available, downloading if necessary.

        Args:
            version_str: Version to ensure is available

        Returns:
            True if version is now available, False if it failed to load
        """
        if self.is_version_available(version_str):
            return True

        # Try to download the requested version
        try:
            from .schema_downloader import download_schema

            schema_path = download_schema(version_str)
            if schema_path:
                self.versions[version_str] = SchemaVersionInfo(
                    version=version_str,
                    path=schema_path,
                    description=f"HPXML Schema v{version_str} (downloaded)",
                    default=False,
                )
                return True
        except ImportError:
            pass

        return False

    def validate_version(self, version_str: str) -> bool:
        """Return True if version string parses and is available."""
        if not version_str:
            return False

        # Check if available
        if not self.is_version_available(version_str):
            return False

        # Validate version format
        try:
            version.parse(version_str)
            return True
        except version.InvalidVersion:
            return False

    def get_compatible_versions(self, min_version: str) -> List[str]:
        """Return versions >= a minimum semantic version string."""
        try:
            min_ver = version.parse(min_version)
            compatible = []

            for ver_str in self.versions:
                try:
                    if version.parse(ver_str) >= min_ver:
                        compatible.append(ver_str)
                except version.InvalidVersion:
                    continue

            return sorted(compatible, key=lambda v: version.parse(v))
        except version.InvalidVersion:
            return []

    def clear_parser_cache(self, version_str: Optional[str] = None) -> None:
        """Invalidate cached parsers for one version or all versions."""
        if version_str:
            # Clear specific version
            to_remove = [
                key for key in self.parsers if key.startswith(f"{version_str}:")
            ]
            for key in to_remove:
                del self.parsers[key]
        else:
            # Clear all
            self.parsers.clear()


# Global version manager instance
_version_manager: Optional[VersionManager] = None


def get_version_manager() -> VersionManager:
    """Return the process-wide singleton VersionManager instance."""
    global _version_manager
    if _version_manager is None:
        _version_manager = VersionManager()
    return _version_manager


def get_versioned_parser(
    version_str: Optional[str] = None, config: Optional[ParserConfig] = None
) -> Optional[CachedSchemaParser]:
    """Shortcut: obtain a parser for a version (default or explicit).

    Args:
        version_str: Desired version; if omitted the manager's default is used.
        config: Optional :class:`ParserConfig` to differentiate parser behavior.

    Returns:
        Cached parser instance or ``None`` if resolution fails.
    """
    manager = get_version_manager()

    if version_str is None:
        version_str = manager.get_default_version()
        if version_str is None:
            return None

    return manager.get_parser(version_str, config)
