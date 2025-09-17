"""HPXML Schema API
====================

High-level toolkit and service layer for programmatic access to **HPXML**
schema structure, validation metadata, and performance‑oriented delivery
through FastAPI and GraphQL endpoints.

Key capabilities
----------------
- Parse HPXML XSD into a normalized tree of :class:`~hpxml_schema_api.models.RuleNode` objects.
- (Pluggable) merge of Schematron / project validation rules.
- Fast in‑process and optional Redis‑backed distributed caching with TTL + file
  staleness detection.
- Version awareness (simultaneous access to multiple HPXML schema versions).
- Performance instrumentation & metrics export endpoints.
- GraphQL bridge for flexible schema exploration.

Design principles
-----------------
1. **Deterministic parsing** – Pure transformations with explicit configuration
    via :class:`~hpxml_schema_api.xsd_parser.ParserConfig`.
2. **Progressive enhancement** – Core XSD parse works without optional
    dependencies (e.g. Redis, psutil). Optional features fail soft.
3. **Cache safety** – ETags incorporate schema path + parser config; file
    modification times invalidate cached artifacts automatically.
4. **Separation of concerns** – Parsing, caching, version management, API
    transport, and monitoring are isolated modules with narrow contracts.

Docstring style
---------------
All public functions, classes, and significant private helpers follow the
Google style docstring convention (Args, Returns, Raises, Examples) to support
downstream auto‑documentation tooling (Sphinx Napoleon / pdoc, etc.).

Minimal quick start
-------------------
>>> from hpxml_schema_api.cache import get_cached_parser
>>> parser = get_cached_parser()
>>> root = parser.parse_xsd('/path/to/HPXML.xsd')
>>> list(child.name for child in root.children)[:5]

FastAPI application instance (for ASGI servers like uvicorn):
>>> from hpxml_schema_api.app import app  # noqa: F401

Public surface
--------------
Only a curated subset is exported at the package level to keep the import
surface stable; advanced modules can be imported explicitly.
"""

__version__ = "0.3.0"

from .cache import get_cached_parser
from .models import RuleNode, ValidationRule
from .xsd_parser import parse_xsd

__all__ = [
    "RuleNode",
    "ValidationRule",
    "parse_xsd",
    "get_cached_parser",
]
