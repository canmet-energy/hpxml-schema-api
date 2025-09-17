"""
CLI commands for HPXML schema management.
"""

import argparse
import sys
import logging
from pathlib import Path

from .schema_downloader import (
    download_schema,
    auto_discover_or_download_schema,
    get_available_versions,
    clear_schema_cache,
    get_cached_schema_path,
    DEFAULT_SCHEMA_VERSION
)

def setup_logging(verbose: bool = False):
    """Setup logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

def cmd_download(args):
    """Download HPXML schema command."""
    setup_logging(args.verbose)

    schema_path = download_schema(args.version, force=args.force)
    if schema_path:
        print(f"✓ Downloaded HPXML schema {args.version} to: {schema_path}")
        return 0
    else:
        print(f"✗ Failed to download HPXML schema {args.version}")
        return 1

def cmd_discover(args):
    """Auto-discover or download HPXML schema command."""
    setup_logging(args.verbose)

    schema_path = auto_discover_or_download_schema(args.version)
    if schema_path:
        print(f"✓ Found HPXML schema: {schema_path}")
        return 0
    else:
        print("✗ No HPXML schema found and download failed")
        return 1

def cmd_list(args):
    """List available schema versions."""
    versions = get_available_versions()
    default_version = DEFAULT_SCHEMA_VERSION
    print("Available HPXML schema versions:")
    for version in versions:
        cached_path = get_cached_schema_path(version)
        status = "cached" if cached_path.exists() else "available"
        marker = "✓" if status == "cached" else "○"
        default_marker = " (default)" if version == default_version else ""
        print(f"  {marker} {version} ({status}){default_marker}")

def cmd_clear(args):
    """Clear schema cache command."""
    setup_logging(args.verbose)

    try:
        clear_schema_cache()
        print("✓ Schema cache cleared")
        return 0
    except Exception as e:
        print(f"✗ Failed to clear cache: {e}")
        return 1

def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="HPXML Schema Management CLI",
        prog="hpxml-schema"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        help="Available commands"
    )

    # Download command
    download_parser = subparsers.add_parser(
        "download",
        help="Download HPXML schema from official repository"
    )
    download_parser.add_argument(
        "--version",
        default=DEFAULT_SCHEMA_VERSION,
        choices=get_available_versions(),
        help=f"Schema version to download (default: {DEFAULT_SCHEMA_VERSION})"
    )
    download_parser.add_argument(
        "--force",
        action="store_true",
        help="Force download even if cached version exists"
    )
    download_parser.set_defaults(func=cmd_download)

    # Discover command
    discover_parser = subparsers.add_parser(
        "discover",
        help="Auto-discover local schema or download if needed"
    )
    discover_parser.add_argument(
        "--version",
        default=DEFAULT_SCHEMA_VERSION,
        choices=get_available_versions(),
        help=f"Preferred schema version if download needed (default: {DEFAULT_SCHEMA_VERSION})"
    )
    discover_parser.set_defaults(func=cmd_discover)

    # List command
    list_parser = subparsers.add_parser(
        "list",
        help="List available schema versions"
    )
    list_parser.set_defaults(func=cmd_list)

    # Clear command
    clear_parser = subparsers.add_parser(
        "clear",
        help="Clear cached schemas"
    )
    clear_parser.set_defaults(func=cmd_clear)

    args = parser.parse_args()

    if not hasattr(args, 'func'):
        parser.print_help()
        return 1

    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())