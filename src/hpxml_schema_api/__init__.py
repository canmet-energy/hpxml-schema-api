"""HPXML Schema API - Programmatic access to HPXML schema metadata and validation.

This package provides tools for:
- Parsing HPXML XSD schemas into structured metadata
- Validating HPXML documents against schema rules
- Dynamic form generation from schema definitions
- High-performance caching and API services

Example usage:
    from hpxml_schema_api.app import app
    from hpxml_schema_api.xsd_parser import parse_xsd
    from hpxml_schema_api.models import RuleNode
"""

__version__ = "0.3.0"

from .models import RuleNode, ValidationRule
from .xsd_parser import parse_xsd
from .cache import get_cached_parser

__all__ = [
    "RuleNode",
    "ValidationRule",
    "parse_xsd",
    "get_cached_parser",
]
