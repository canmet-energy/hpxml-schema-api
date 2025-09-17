"""Automatic HPXML schema acquisition & local caching.

This helper centralizes logic for locating or downloading HPXML XSD files. It
implements a pragmatic multi-step discovery strategy before hitting the
network and caches downloaded versions under ``~/.cache/hpxml-schema-api``.

Discovery order:
        1. Explicit path via ``HPXML_SCHEMA_PATH`` environment variable
        2. Version override via ``HPXML_SCHEMA_VERSION`` (affects download target)
        3. Common OpenStudio‑HPXML installation paths
        4. Existing cached download (versioned file)
        5. Fresh download from the official HPXML Working Group repository

Example:
        from hpxml_schema_api.schema_downloader import auto_discover_or_download_schema
        path = auto_discover_or_download_schema("4.0")
        if path:
                print("Using schema at", path)
        else:
                raise RuntimeError("Failed to obtain HPXML schema")

Notes:
* A minimal validation check ensures the downloaded file contains the XML
    Schema namespace string to reduce accidental HTML/error-page caching.
* Additional checksum or signature verification could be layered easily if
    future integrity requirements arise.
"""

import hashlib
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Official HPXML schema URLs
HPXML_SCHEMA_URLS = {
    "4.0": "https://raw.githubusercontent.com/hpxmlwg/hpxml/v4.0/schemas/HPXML.xsd",
    "4.1": "https://raw.githubusercontent.com/hpxmlwg/hpxml/v4.1/schemas/HPXML.xsd",
    "latest": "https://raw.githubusercontent.com/hpxmlwg/hpxml/master/schemas/HPXML.xsd",
}

DEFAULT_SCHEMA_VERSION = "4.0"


def get_schema_cache_dir() -> Path:
    """Return (and create if needed) the local schema cache directory."""
    cache_dir = Path.home() / ".cache" / "hpxml-schema-api" / "schemas"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_cached_schema_path(version: str = DEFAULT_SCHEMA_VERSION) -> Path:
    """Compute the expected cached pathname for a version string."""
    cache_dir = get_schema_cache_dir()
    return cache_dir / f"HPXML_{version}.xsd"


def download_schema(
    version: str = DEFAULT_SCHEMA_VERSION, force: bool = False
) -> Optional[Path]:
    """Download a specific HPXML schema version into the local cache.

    Args:
        version: Target schema version key (must exist in ``HPXML_SCHEMA_URLS``).
        force: If True, redownload even if a cached file already exists.

    Returns:
        Path to the downloaded (or cached) schema file; ``None`` on failure.
    """
    if version not in HPXML_SCHEMA_URLS:
        logger.error(
            f"Unknown schema version: {version}. Available: {list(HPXML_SCHEMA_URLS.keys())}"
        )
        return None

    cached_path = get_cached_schema_path(version)

    # Use cached version if available and not forcing download
    if cached_path.exists() and not force:
        logger.info(f"Using cached HPXML schema: {cached_path}")
        return cached_path

    url = HPXML_SCHEMA_URLS[version]
    logger.info(f"Downloading HPXML schema {version} from {url}")

    try:
        # Download schema
        with urllib.request.urlopen(url, timeout=30) as response:
            schema_content = response.read()

        # Verify it's a valid XSD by checking for XML schema namespace
        content_str = schema_content.decode("utf-8")
        if "http://www.w3.org/2001/XMLSchema" not in content_str:
            logger.error("Downloaded file does not appear to be a valid XSD schema")
            return None

        # Write to cache
        with open(cached_path, "wb") as f:
            f.write(schema_content)

        logger.info(f"Downloaded and cached HPXML schema {version} to {cached_path}")
        return cached_path

    except urllib.error.URLError as e:
        logger.error(f"Failed to download schema from {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error downloading schema: {e}")
        return None


def auto_discover_or_download_schema(
    prefer_version: str = DEFAULT_SCHEMA_VERSION,
) -> Optional[Path]:
    """Locate an HPXML schema via discovery cascade or download fallback.

    Args:
        prefer_version: Version used if a network fetch becomes necessary.

    Returns:
        Resolved schema file path or ``None`` if all strategies fail.

    Example:
        path = auto_discover_or_download_schema("4.1")
        if not path:
            print("Could not resolve schema")
    """
    # Check for version preference from environment
    env_version = os.getenv("HPXML_SCHEMA_VERSION")
    if env_version and env_version in HPXML_SCHEMA_URLS:
        prefer_version = env_version
        logger.info(f"Using schema version from environment: {prefer_version}")

    # 1. Check environment variable
    env_path = os.getenv("HPXML_SCHEMA_PATH")
    if env_path:
        path = Path(env_path).expanduser()
        if path.exists():
            logger.info(f"Using HPXML schema from environment: {path}")
            return path
        else:
            logger.warning(f"HPXML_SCHEMA_PATH set but file not found: {path}")

    # 2. Try common OpenStudio-HPXML installation paths
    common_paths = [
        Path.home()
        / ".local/share/OpenStudio-HPXML-v1.9.1/HPXMLtoOpenStudio/resources/hpxml_schema/HPXML.xsd",
        Path(
            "/usr/local/openstudio/HPXMLtoOpenStudio/resources/hpxml_schema/HPXML.xsd"
        ),
        Path("/opt/openstudio/HPXMLtoOpenStudio/resources/hpxml_schema/HPXML.xsd"),
    ]

    for path in common_paths:
        if path.exists():
            logger.info(f"Found HPXML schema at: {path}")
            return path

    # 3. Check for cached downloaded schema
    cached_path = get_cached_schema_path(prefer_version)
    if cached_path.exists():
        logger.info(f"Using cached HPXML schema: {cached_path}")
        return cached_path

    # 4. Download from official repository
    logger.info("No local HPXML schema found, downloading from official repository...")
    return download_schema(prefer_version)


def get_available_versions() -> List[str]:
    """Return the list of version identifiers supported for download."""
    return list(HPXML_SCHEMA_URLS.keys())


def clear_schema_cache() -> None:
    """Delete all cached schema files from the local cache directory."""
    cache_dir = get_schema_cache_dir()
    for schema_file in cache_dir.glob("HPXML_*.xsd"):
        schema_file.unlink()
        logger.info(f"Removed cached schema: {schema_file}")
