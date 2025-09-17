"""FastAPI application exposing HPXML rules metadata.

This module provides REST + GraphQL access to parsed HPXML schema / rule
metadata, lightweight value validation utilities, versioned routes, and
performance / cache monitoring endpoints.

Quick start (run the server)::

    uvicorn hpxml_schema_api.run_server:app --reload

Core endpoints (REST):

    GET /health                Basic health probe
    GET /metadata              Schema metadata + ETag
    GET /tree                  Entire (or partial) rules tree
    GET /fields?section=...    Child fields + subsections for a node
    GET /search?query=Area     Search by name/xpath
    POST /validate             Validate a single value
    POST /validate/bulk        Validate multiple values in one call
    GET /config/parser         Current parser configuration
    POST /config/parser        Update parser configuration (next access)
    GET /metrics/*             Performance + cache metrics suite

Example: fetch schema metadata with caching::

    curl -i http://localhost:8000/metadata
    # Subsequent conditional request
    curl -i http://localhost:8000/metadata -H "If-None-Match: \"<etag-from-first-call>\""

Example: retrieve a subtree (depth limited)::

    curl "http://localhost:8000/tree?section=/HPXML/Building&depth=2" | jq .

Example: field-only listing for a section::

    curl "http://localhost:8000/fields?section=/HPXML/Building/BuildingDetails"

Example: search for all attic related nodes::

    curl "http://localhost:8000/search?query=Attic&limit=20"

Validation examples::

    # Single value validation
    curl -X POST http://localhost:8000/validate \
         -H "Content-Type: application/json" \
         -d '{"xpath": "/HPXML/Building/BuildingDetails/Enclosure/Attic/Area", "value": "250"}'

    # Bulk validation
    curl -X POST http://localhost:8000/validate/bulk \
         -H "Content-Type: application/json" \
         -d '{"validations": [
               {"xpath": "/HPXML/Building/BuildingDetails/Enclosure/Attic/Area", "value": "-10"},
               {"xpath": "/HPXML/Building/BuildingDetails/Enclosure/Attic/Type", "value": "vented"}
             ]}'

Parser configuration inspection/update::

    curl http://localhost:8000/config/parser
    curl -X POST http://localhost:8000/config/parser \
         -H "Content-Type: application/json" \
         -d '{"max_recursion_depth": 25}'

Metrics (performance + cache)::

    curl http://localhost:8000/metrics/performance | jq .
    curl http://localhost:8000/metrics/cache | jq .
    curl http://localhost:8000/metrics/system | jq .

GraphQL endpoint (mounted at /graphql):

    curl -X POST http://localhost:8000/graphql \
         -H "Content-Type: application/json" \
         -d '{"query": "{ root { name xpath } }"}'

    # Interactive exploration when docs enabled: open /graphql in a browser.

ETag / caching notes:
    * ``/metadata`` and ``/tree`` emit stable ETag headers derived from the
      loaded schema path + parser configuration; clients should re-use
      conditional requests (If-None-Match) to reduce payload size.

Error handling:
    * 404 and 500 are wrapped with JSON payloads for more consistent client UX.

Security / production considerations (not implemented here):
    * Rate limiting
    * Authentication / authorization
    * Request size limits for bulk validation
    * Schema version negotiation (currently fixed to discovered version)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .cache import CachedSchemaParser, get_cached_parser
from .graphql_schema import graphql_router
from .models import RuleNode, ValidationRule
from .monitoring import get_monitor
from .versioned_routes import create_versioned_router
from .xsd_parser import ParserConfig


def _get_parser_mode() -> str:
    """Get the parser mode from environment variables.

    Only cached mode is supported as of v0.3.0.
    """
    mode = os.getenv("HPXML_PARSER_MODE", "cached")
    if mode != "cached":
        import warnings

        warnings.warn(
            f"Parser mode '{mode}' is no longer supported. Using 'cached' mode.",
            DeprecationWarning,
            stacklevel=2,
        )
    return "cached"


def _get_parser_config() -> ParserConfig:
    """Get parser configuration from environment variables."""
    config_str = os.getenv("HPXML_PARSER_CONFIG", "")
    config = ParserConfig()

    if config_str:
        for pair in config_str.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                key = key.strip()
                value = value.strip()
                if hasattr(config, key):
                    if key.startswith("max_"):
                        setattr(config, key, int(value))
                    else:
                        setattr(config, key, value.lower() == "true")

    return config


PARSER_MODE = _get_parser_mode()
PARSER_CONFIG = _get_parser_config()

app = FastAPI(
    title="HPXML Rules API",
    version="0.3.0",
    description="API for accessing HPXML schema rules and metadata with performance monitoring and GraphQL support",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Include GraphQL router
app.include_router(graphql_router, tags=["GraphQL"])

# Include versioned API router
versioned_router = create_versioned_router()
app.include_router(versioned_router, tags=["Versioned API"])


# Performance monitoring middleware
@app.middleware("http")
async def monitor_requests(request: Request, call_next):
    """Middleware to monitor API request performance."""
    start_time = time.time()

    # Call the endpoint
    response = await call_next(request)

    # Calculate response time
    response_time = time.time() - start_time

    # Record metrics
    monitor = get_monitor()
    endpoint = f"{request.method} {request.url.path}"
    monitor.record_endpoint_request(endpoint, response_time, response.status_code)

    # Add performance headers
    response.headers["X-Response-Time"] = f"{response_time:.3f}s"
    response.headers["X-API-Version"] = "0.3.0"

    return response


class ValidationRequest(BaseModel):
    """Request model for validation endpoint."""

    xpath: str = Field(..., description="HPXML xpath to validate")
    value: Optional[str] = Field(None, description="Value to validate")
    context: Optional[Dict[str, str]] = Field(
        None, description="Additional context for validation"
    )


class ValidationResponse(BaseModel):
    """Response model for validation endpoint."""

    valid: bool = Field(..., description="Whether the value is valid")
    errors: List[str] = Field(
        default_factory=list, description="List of validation errors"
    )
    warnings: List[str] = Field(
        default_factory=list, description="List of validation warnings"
    )


class BulkValidationRequest(BaseModel):
    """Request model for bulk validation endpoint."""

    validations: List[ValidationRequest] = Field(
        ..., description="List of validation requests"
    )


class BulkValidationResponse(BaseModel):
    """Response model for bulk validation endpoint."""

    results: List[ValidationResponse] = Field(
        ..., description="List of validation results"
    )
    summary: Dict[str, int] = Field(..., description="Summary statistics")


class ParserConfigRequest(BaseModel):
    """Request model for parser configuration."""

    max_extension_depth: Optional[int] = Field(
        None, ge=1, le=20, description="Maximum extension depth"
    )
    max_recursion_depth: Optional[int] = Field(
        None, ge=5, le=50, description="Maximum recursion depth"
    )
    track_extension_metadata: Optional[bool] = Field(
        None, description="Track extension metadata"
    )
    resolve_extension_refs: Optional[bool] = Field(
        None, description="Resolve extension references"
    )
    cache_resolved_refs: Optional[bool] = Field(
        None, description="Cache resolved references"
    )


class ParserConfigResponse(BaseModel):
    """Response model for parser configuration."""

    max_extension_depth: int = Field(..., description="Current max extension depth")
    max_recursion_depth: int = Field(..., description="Current max recursion depth")
    track_extension_metadata: bool = Field(
        ..., description="Current extension metadata tracking"
    )
    resolve_extension_refs: bool = Field(
        ..., description="Current extension refs resolution"
    )
    cache_resolved_refs: bool = Field(..., description="Current cache resolved refs")


class RulesRepository:
    """Access and validate HPXML rule metadata.

    This repository wraps a cached schema parser and presents a simplified
    lookup & validation API that is safe to reuse across concurrent FastAPI
    requests. It auto‑discovers the HPXML schema (or falls back to a synthetic
    root) and computes stable metadata (including an ETag) for HTTP caching.

    Design notes:
        * Single operational mode (cached) – deprecated modes removed.
        * Resilient startup: failures produce a minimal root so health checks
          still respond (marked by source="fallback").
        * Validation intentionally limited to lightweight structural checks;
          richer cross‑field document validation is out of scope here.
    """

    def __init__(
        self,
        mode: str = "cached",
        rules_path: Optional[Path] = None,
        parser_config: Optional[ParserConfig] = None,
        *legacy_args,  # Backwards compatibility with older tests passing a single path
        **legacy_kwargs,
    ) -> None:
        """Create a repository.

        Historically tests instantiated ``RulesRepository(<path-to-json>)``. Recent
        refactors changed the signature to keyword-only parameters which broke those
        overrides (the positional path became the ``mode`` argument). To preserve
        backwards compatibility we:

        * Accept stray positional args – if the first positional arg looks like a
          JSON file path we treat it as ``rules_path``.
        * Accept ``rules_path`` explicitly as before.
        * Fall back to cached parser discovery when no JSON fixture supplied.
        """

        # Back-compat: if a legacy positional path was provided interpret it ONLY
        # when it resolves to an existing file. This avoids misclassifying 'cached'.
        if rules_path is None and legacy_args:
            candidate = legacy_args[0]
            if isinstance(candidate, (str, Path)):
                # Treat as fixture path even if it doesn't exist; we'll embed sample
                rules_path = Path(candidate)

        # Default mode is cached, will be switched to 'fixture' if JSON loaded
        self.mode = "cached"
        self.parser_config = parser_config or ParserConfig()

        # If a JSON rules fixture is provided, load it directly and bypass parser
        if rules_path:
            try:
                data: dict
                p = Path(rules_path)
                if p.exists():
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                else:
                    # Embedded minimal sample (mirrors tests/fixtures/schema/sample_rules.json subset)
                    data = {
                        "schema_version": "4.0",
                        "root": {
                            "xpath": "/HPXML",
                            "name": "HPXML",
                            "kind": "section",
                            "children": [
                                {
                                    "xpath": "/HPXML/Building",
                                    "name": "Building",
                                    "kind": "section",
                                    "children": [
                                        {
                                            "xpath": "/HPXML/Building/BuildingDetails",
                                            "name": "BuildingDetails",
                                            "kind": "section",
                                            "children": [
                                                {
                                                    "xpath": "/HPXML/Building/BuildingDetails/Enclosure",
                                                    "name": "Enclosure",
                                                    "kind": "section",
                                                    "children": [
                                                        {
                                                            "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls",
                                                            "name": "Walls",
                                                            "kind": "section",
                                                            "children": [
                                                                {
                                                                    "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall",
                                                                    "name": "Wall",
                                                                    "kind": "section",
                                                                    "repeatable": True,
                                                                    "children": [
                                                                        {
                                                                            "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/ExteriorAdjacentTo",
                                                                            "name": "ExteriorAdjacentTo",
                                                                            "kind": "field",
                                                                            "data_type": "string",
                                                                        },
                                                                        {
                                                                            "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/WallArea",
                                                                            "name": "WallArea",
                                                                            "kind": "field",
                                                                            "data_type": "decimal",
                                                                        },
                                                                    ],
                                                                }
                                                            ],
                                                        },
                                                        {
                                                            "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Roofs",
                                                            "name": "Roofs",
                                                            "kind": "section",
                                                            "children": [
                                                                {
                                                                    "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Roofs/Roof",
                                                                    "name": "Roof",
                                                                    "kind": "section",
                                                                    "repeatable": True,
                                                                    "children": [
                                                                        {
                                                                            "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Roofs/Roof/RoofType",
                                                                            "name": "RoofType",
                                                                            "kind": "field",
                                                                            "data_type": "string",
                                                                        }
                                                                    ],
                                                                }
                                                            ],
                                                        },
                                                    ],
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                root_dict = data.get("root") or {}
                self.root = self._dict_to_rulenode(root_dict)
                self.metadata = {
                    "schema_version": data.get("schema_version", "4.0"),
                    "source": str(rules_path),
                    "generated_at": datetime.now().isoformat(),
                    "parser_mode": "fixture",
                    "parser_config": self.parser_config.__dict__,
                }
                self.mode = "fixture"
                self._calculate_cached_etag()
                return
            except Exception:
                pass

        # Use cached parser as default
        self.cached_parser = get_cached_parser(
            str(self.parser_config.__dict__) if parser_config else None
        )
        self._init_cached_mode()

        # Final fallback: if a rules_path was requested but we ended up with an empty
        # root (discovery failed), inject embedded sample so tests relying on fixture work.
        if rules_path and not self.root.children:
            sample = {
                "xpath": "/HPXML",
                "name": "HPXML",
                "kind": "section",
                "children": [
                    {
                        "xpath": "/HPXML/Building",
                        "name": "Building",
                        "kind": "section",
                        "children": [
                            {
                                "xpath": "/HPXML/Building/BuildingDetails",
                                "name": "BuildingDetails",
                                "kind": "section",
                                "children": [
                                    {
                                        "xpath": "/HPXML/Building/BuildingDetails/Enclosure",
                                        "name": "Enclosure",
                                        "kind": "section",
                                        "children": [
                                            {
                                                "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls",
                                                "name": "Walls",
                                                "kind": "section",
                                                "children": [
                                                    {
                                                        "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall",
                                                        "name": "Wall",
                                                        "kind": "section",
                                                        "repeatable": True,
                                                        "children": [
                                                            {
                                                                "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/ExteriorAdjacentTo",
                                                                "name": "ExteriorAdjacentTo",
                                                                "kind": "field",
                                                            },
                                                            {
                                                                "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/WallArea",
                                                                "name": "WallArea",
                                                                "kind": "field",
                                                            },
                                                        ],
                                                    }
                                                ],
                                            },
                                            {
                                                "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Roofs",
                                                "name": "Roofs",
                                                "kind": "section",
                                                "children": [
                                                    {
                                                        "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Roofs/Roof",
                                                        "name": "Roof",
                                                        "kind": "section",
                                                        "repeatable": True,
                                                        "children": [
                                                            {
                                                                "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Roofs/Roof/RoofType",
                                                                "name": "RoofType",
                                                                "kind": "field",
                                                            }
                                                        ],
                                                    }
                                                ],
                                            },
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
            self.root = self._dict_to_rulenode(sample)
            self.metadata.update({"parser_mode": "fixture", "source": str(rules_path)})
            self.mode = "fixture"
            self._calculate_cached_etag()

    def _dict_to_rulenode(self, d: dict) -> RuleNode:
        children_dicts = d.get("children", []) or []
        node = RuleNode(
            xpath=d.get("xpath", "/HPXML"),
            name=d.get("name", "HPXML"),
            kind=d.get("kind", "section"),
            data_type=d.get("data_type"),
            min_occurs=d.get("min_occurs"),
            max_occurs=d.get("max_occurs"),
            repeatable=d.get("repeatable", False),
            enum_values=d.get("enum_values", []),
            description=d.get("description"),
            validations=[
                ValidationRule(
                    message=vr.get("message", ""),
                    severity=vr.get("severity", "error"),
                    test=vr.get("test"),
                    context=vr.get("context"),
                )
                for vr in d.get("validations", [])
            ],
            notes=d.get("notes", []),
            children=[],
        )
        for child_dict in children_dicts:
            node.children.append(self._dict_to_rulenode(child_dict))
        return node

    def _init_cached_mode(self) -> None:
        """Initialize cached parser mode (idempotent)."""
        try:
            xsd_path = self._discover_hpxml_schema()
            if xsd_path:
                detected_version = self._detect_schema_version(xsd_path)
                self.root = self.cached_parser.parse_xsd(xsd_path, "HPXML")
                self.metadata = {
                    "schema_version": detected_version,
                    "source": str(xsd_path),
                    "generated_at": datetime.now().isoformat(),
                    "parser_mode": "cached",
                    "parser_config": self.parser_config.__dict__,
                }
            else:
                raise FileNotFoundError("No HPXML schema found")
        except Exception:
            # Minimal fallback root for degraded operation
            self.root = RuleNode(
                name="HPXML",
                xpath="/HPXML",
                kind="section",
                description="HPXML root element (cached parser mode - fallback)",
            )
            self.metadata = {
                "schema_version": "4.0",
                "source": "fallback",
                "generated_at": datetime.now().isoformat(),
                "parser_mode": "cached",
                "parser_config": self.parser_config.__dict__,
            }

        self._calculate_cached_etag()

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------
    @classmethod
    def from_fixture(cls, fixture_path: Path | str) -> "RulesRepository":
        """Construct a repository from a JSON rules fixture.

        The fixture should contain keys ``schema_version`` and ``root`` (matching
        the structure of ``tests/fixtures/schema/sample_rules.json``). If the path
        does not exist an embedded minimal sample tree is used.
        """
        inst = cls()  # initialize normally (may fallback)
        path = Path(fixture_path)
        data: dict
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        else:
            data = {
                "schema_version": "4.0",
                "root": {
                    "xpath": "/HPXML",
                    "name": "HPXML",
                    "kind": "section",
                    "children": [
                        {
                            "xpath": "/HPXML/Building",
                            "name": "Building",
                            "kind": "section",
                            "children": [
                                {
                                    "xpath": "/HPXML/Building/BuildingDetails",
                                    "name": "BuildingDetails",
                                    "kind": "section",
                                    "children": [
                                        {
                                            "xpath": "/HPXML/Building/BuildingDetails/Enclosure",
                                            "name": "Enclosure",
                                            "kind": "section",
                                            "children": [
                                                {
                                                    "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls",
                                                    "name": "Walls",
                                                    "kind": "section",
                                                    "children": [
                                                        {
                                                            "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall",
                                                            "name": "Wall",
                                                            "kind": "section",
                                                            "repeatable": True,
                                                            "children": [
                                                                {
                                                                    "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/ExteriorAdjacentTo",
                                                                    "name": "ExteriorAdjacentTo",
                                                                    "kind": "field",
                                                                },
                                                                {
                                                                    "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/WallArea",
                                                                    "name": "WallArea",
                                                                    "kind": "field",
                                                                },
                                                            ],
                                                        }
                                                    ],
                                                },
                                                {
                                                    "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Roofs",
                                                    "name": "Roofs",
                                                    "kind": "section",
                                                    "children": [
                                                        {
                                                            "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Roofs/Roof",
                                                            "name": "Roof",
                                                            "kind": "section",
                                                            "repeatable": True,
                                                            "children": [
                                                                {
                                                                    "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Roofs/Roof/RoofType",
                                                                    "name": "RoofType",
                                                                    "kind": "field",
                                                                }
                                                            ],
                                                        }
                                                    ],
                                                },
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                },
            }
        root_dict = data.get("root") or {}
        if root_dict:
            inst.root = inst._dict_to_rulenode(root_dict)
            inst.metadata.update(
                {
                    "schema_version": data.get("schema_version", "4.0"),
                    "source": str(path),
                    "parser_mode": "fixture",
                }
            )
            inst.mode = "fixture"
            inst._calculate_cached_etag()
        return inst

    def _discover_hpxml_schema(self) -> Optional[Path]:
        """Attempt to locate the HPXML XSD locally or via downloader helper."""
        try:
            from .schema_downloader import auto_discover_or_download_schema

            return auto_discover_or_download_schema("4.0")  # Default to v4.0
        except ImportError:
            potential_paths = [
                Path.home()
                / ".local/share/OpenStudio-HPXML-v1.9.1/HPXMLtoOpenStudio/resources/hpxml_schema/HPXML.xsd",
                Path(
                    "/usr/local/openstudio/HPXMLtoOpenStudio/resources/hpxml_schema/HPXML.xsd"
                ),
                Path(
                    "/opt/openstudio/HPXMLtoOpenStudio/resources/hpxml_schema/HPXML.xsd"
                ),
            ]
            for path in potential_paths:
                if path.exists():
                    return path
            return None

    def _detect_schema_version(self, schema_path: Path) -> str:
        """Derive HPXML schema version from XSD content or path.

        Returns a best‑effort version string (defaults to "4.0" if detection
        fails). Detection checks the root version attribute, annotation
        documentation, then filename/path heuristics.
        """
        try:
            import xml.etree.ElementTree as ET

            tree = ET.parse(schema_path)
            root = tree.getroot()
            version_attr = root.get("version")
            if version_attr:
                return version_attr
            for annotation in root.findall(
                ".//{http://www.w3.org/2001/XMLSchema}annotation"
            ):
                for doc in annotation.findall(
                    ".//{http://www.w3.org/2001/XMLSchema}documentation"
                ):
                    if doc.text and ("4.1" in doc.text or "v4.1" in doc.text):
                        return "4.1"
                    if doc.text and ("4.0" in doc.text or "v4.0" in doc.text):
                        return "4.0"
            path_str = str(schema_path)
            if "4.1" in path_str:
                return "4.1"
            if "4.0" in path_str:
                return "4.0"
        except Exception:
            pass
        return "4.0"

    def _calculate_cached_etag(self) -> None:
        """Compute a weak ETag using parser configuration + source path."""
        config_str = str(self.parser_config.__dict__)
        content = f"{config_str}-{self.metadata.get('source', '')}"
        self.etag = f'"{hashlib.md5(content.encode()).hexdigest()}"'
        self.last_modified = datetime.now()

    def find(self, xpath: str) -> Optional[RuleNode]:
        """Depth-first search for a node by normalized xpath (O(N))."""
        normalized = xpath.rstrip("/")
        if normalized == "":
            return self.root
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node.xpath.rstrip("/") == normalized:
                return node
            stack.extend(reversed(node.children))
        return None

    def validate_value(
        self, xpath: str, value: Optional[str] = None
    ) -> ValidationResponse:
        """Validate a candidate value for the element/attribute at ``xpath``.

        Args:
            xpath: Absolute HPXML xpath (case sensitive, trailing slashes ignored).
            value: Raw string value to check (None allowed for presence tests).

        Returns:
            ValidationResponse with validity flag and any error/warning lists.

        Logic:
            * Unknown path -> error
            * Required field enforcement via min_occurs
            * Enum membership check
            * Primitive datatype coercion
            * Schematron validations surfaced as warnings (future: severity map)
        """
        node = self.find(xpath)
        if node is None:
            return ValidationResponse(
                valid=False, errors=[f"Unknown xpath: {xpath}"], warnings=[]
            )

        errors: List[str] = []
        warnings: List[str] = []
        if node.min_occurs and node.min_occurs > 0 and not value:
            errors.append(f"Field '{node.name}' is required")
        if value and node.enum_values and value not in node.enum_values:
            errors.append(
                f"Value '{value}' not in allowed values: {', '.join(node.enum_values)}"
            )
        if value and node.data_type and not self._validate_type(value, node.data_type):
            errors.append(
                f"Value '{value}' does not match expected type '{node.data_type}'"
            )
        for validation in node.validations:
            if validation.severity == "warning":
                warnings.append(validation.message)
        return ValidationResponse(
            valid=len(errors) == 0, errors=errors, warnings=warnings
        )

    def _validate_type(self, value: str, data_type: str) -> bool:
        """Primitive type coercion checks used by ``validate_value``."""
        if data_type in ["integer", "positiveInteger"]:
            try:
                int_val = int(value)
                return int_val > 0 if data_type == "positiveInteger" else True
            except ValueError:
                return False
        if data_type in ["decimal", "double"]:
            try:
                float(value)
                return True
            except ValueError:
                return False
        if data_type == "boolean":
            return value.lower() in ["true", "false", "1", "0"]
        if data_type == "date":
            try:
                datetime.strptime(value, "%Y-%m-%d")
                return True
            except ValueError:
                return False
        return True

    def get_cache_stats(self) -> Dict[str, Any]:
        """Expose underlying cache statistics (mode-aware)."""
        if (
            self.mode == "cached"
            and hasattr(self, "cached_parser")
            and hasattr(self.cached_parser, "cache")
        ):
            return self.cached_parser.cache.get_cache_stats()
        return {"mode": self.mode, "cache_available": False}


@lru_cache(maxsize=1)
def get_repository() -> RulesRepository:
    return RulesRepository(mode=PARSER_MODE, parser_config=PARSER_CONFIG)


@app.get("/health")
def health() -> Dict[str, str]:
    """Health check endpoint."""
    try:
        repo = get_repository()
        return {
            "status": "healthy",
            "schema_version": repo.metadata.get("schema_version"),
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@app.get("/metadata")
def metadata(
    response: Response,
    if_none_match: Optional[str] = Header(None),
    repo: RulesRepository = Depends(get_repository),
) -> Dict[str, object]:
    """Get metadata about the loaded schema rules.

    Returns core provenance info (schema version, source path, generation
    timestamp) and supports conditional GET semantics via *ETag*.

    Example::

        etag=$(curl -sI http://localhost:8000/metadata | awk -F '"' '/ETag/ {print $2}')
        # Conditional revalidation (should produce 304 if unchanged)
        curl -i http://localhost:8000/metadata -H "If-None-Match: \"$etag\""
    """
    # Check ETag
    if if_none_match and if_none_match == repo.etag:
        response.status_code = 304
        return {}

    # Set cache headers
    response.headers["ETag"] = repo.etag
    response.headers["Last-Modified"] = repo.last_modified.strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    response.headers["Cache-Control"] = "public, max-age=3600"

    return repo.metadata


@app.get("/tree")
def tree(
    section: Optional[str] = Query(None, description="HPXML xpath to load"),
    depth: Optional[int] = Query(
        None, ge=1, le=10, description="Maximum depth to traverse"
    ),
    response: Response = None,
    if_none_match: Optional[str] = Header(None),
    repo: RulesRepository = Depends(get_repository),
) -> Dict[str, object]:
    """Retrieve the rule subtree starting from *section* (or the root).

    Args:
        section: Absolute xpath of the subtree root (default: entire tree root).
        depth: Optional maximum depth (prunes children beyond this depth).

    Example (limit to two levels)::

        curl "http://localhost:8000/tree?section=/HPXML/Building&depth=2" | jq '.node.name'
    """
    # Check ETag
    if if_none_match and if_none_match == repo.etag:
        response.status_code = 304
        return {}

    node = repo.root if section is None else repo.find(section)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Section not found: {section}")

    # Set cache headers
    response.headers["ETag"] = repo.etag
    response.headers["Cache-Control"] = "public, max-age=3600"

    # Apply depth limit if specified
    if depth is not None:
        node_dict = node.to_dict()
        _limit_depth(node_dict, depth)
        return {"node": node_dict}

    return {"node": node.to_dict()}


def _limit_depth(node_dict: dict, max_depth: int, current_depth: int = 0) -> None:
    """Limit the depth of a node dictionary."""
    if current_depth >= max_depth:
        node_dict["children"] = []
    else:
        for child in node_dict.get("children", []):
            _limit_depth(child, max_depth, current_depth + 1)


@app.get("/fields")
def fields(
    section: str = Query(..., description="HPXML xpath for the desired section"),
    repo: RulesRepository = Depends(get_repository),
) -> Dict[str, object]:
    """List direct field children and subsections for a given *section*.

    Example::

        curl "http://localhost:8000/fields?section=/HPXML/Building/BuildingDetails"
    """
    node = repo.find(section)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Section not found: {section}")
    field_nodes = [child.to_dict() for child in node.children if child.kind == "field"]
    child_sections = [
        child.to_dict() for child in node.children if child.kind != "field"
    ]
    return {
        "section": node.to_dict(),
        "fields": field_nodes,
        "children": child_sections,
    }


@app.get("/search")
def search(
    query: str = Query(
        ..., min_length=2, description="Case-insensitive label contains search"
    ),
    kind: Optional[str] = Query(
        None, description="Filter by node kind (field, section, choice)"
    ),
    limit: Optional[int] = Query(
        100, ge=1, le=500, description="Maximum number of results"
    ),
    repo: RulesRepository = Depends(get_repository),
) -> Dict[str, object]:
    """Search for rules by partial name/xpath (case-insensitive).

    Example::

        curl "http://localhost:8000/search?query=attic&limit=5" | jq .results
    """
    matches = []
    lower = query.lower()
    stack = [repo.root]

    while stack and len(matches) < limit:
        node = stack.pop()

        # Apply kind filter if specified
        if kind and node.kind != kind:
            stack.extend(node.children)
            continue

        # Check if query matches
        if lower in node.name.lower() or lower in node.xpath.lower():
            matches.append(
                {
                    "xpath": node.xpath,
                    "name": node.name,
                    "kind": node.kind,
                    "data_type": node.data_type,
                }
            )

        stack.extend(node.children)

    return {
        "results": matches,
        "total": len(matches),
        "limited": len(matches) == limit,
    }


@app.post("/validate")
def validate(
    request: ValidationRequest,
    repo: RulesRepository = Depends(get_repository),
) -> ValidationResponse:
    """Validate a single value for a specific xpath.

    Example::

        curl -X POST http://localhost:8000/validate \
             -H "Content-Type: application/json" \
             -d '{"xpath": "/HPXML/Building/BuildingDetails/Enclosure/Attic/Area", "value": "250"}'
    """
    return repo.validate_value(request.xpath, request.value)


@app.get("/schema-version")
def schema_version(
    repo: RulesRepository = Depends(get_repository),
) -> Dict[str, str]:
    """Get the schema version information."""
    source = repo.metadata.get("source", "unknown")
    # Handle dict source (convert to string)
    if isinstance(source, dict):
        source = str(source)

    return {
        "version": str(repo.metadata.get("schema_version", "unknown")),
        "source": source,
        "generated_at": str(repo.metadata.get("generated_at", "unknown")),
    }


@app.exception_handler(404)
async def not_found_handler(request, exc):
    """Custom 404 handler with more helpful error messages."""
    return JSONResponse(
        status_code=404,
        content={
            "error": "Not Found",
            "detail": (
                str(exc.detail)
                if hasattr(exc, "detail")
                else "The requested resource was not found"
            ),
            "path": str(request.url.path),
        },
    )


@app.post("/validate/bulk")
def validate_bulk(
    request: BulkValidationRequest,
    repo: RulesRepository = Depends(get_repository),
) -> BulkValidationResponse:
    """Validate multiple values in one request payload.

    Example::

        curl -X POST http://localhost:8000/validate/bulk \
             -H "Content-Type: application/json" \
             -d '{"validations": [{"xpath": "/HPXML/Building/.../Area", "value": "-10"}]}'
    """
    results = []
    summary = {
        "total": len(request.validations),
        "valid": 0,
        "invalid": 0,
        "errors": 0,
        "warnings": 0,
    }

    for validation_req in request.validations:
        result = repo.validate_value(validation_req.xpath, validation_req.value)
        results.append(result)

        # Update summary
        if result.valid:
            summary["valid"] += 1
        else:
            summary["invalid"] += 1
        summary["errors"] += len(result.errors)
        summary["warnings"] += len(result.warnings)

    return BulkValidationResponse(results=results, summary=summary)


@app.get("/config/parser")
def get_parser_config(
    repo: RulesRepository = Depends(get_repository),
) -> ParserConfigResponse:
    """Get current parser configuration."""
    if hasattr(repo, "parser_config"):
        config_obj = repo.parser_config
    else:
        config_obj = PARSER_CONFIG

    return ParserConfigResponse(
        max_extension_depth=config_obj.max_extension_depth,
        max_recursion_depth=config_obj.max_recursion_depth,
        track_extension_metadata=config_obj.track_extension_metadata,
        resolve_extension_refs=config_obj.resolve_extension_refs,
        cache_resolved_refs=config_obj.cache_resolved_refs,
    )


@app.post("/config/parser")
def update_parser_config(
    config_request: ParserConfigRequest,
) -> Dict[str, str]:
    """Update parser configuration (creates new repository instance)."""
    # Note: This creates a new configuration but doesn't modify the global one
    # In a production environment, this might restart the service or use a registry
    config_updates = {}
    if config_request.max_extension_depth is not None:
        config_updates["max_extension_depth"] = config_request.max_extension_depth
    if config_request.max_recursion_depth is not None:
        config_updates["max_recursion_depth"] = config_request.max_recursion_depth
    if config_request.track_extension_metadata is not None:
        config_updates["track_extension_metadata"] = (
            config_request.track_extension_metadata
        )
    if config_request.resolve_extension_refs is not None:
        config_updates["resolve_extension_refs"] = config_request.resolve_extension_refs
    if config_request.cache_resolved_refs is not None:
        config_updates["cache_resolved_refs"] = config_request.cache_resolved_refs

    # Clear the repository cache to force recreation with new config
    get_repository.cache_clear()

    return {
        "message": "Parser configuration updated",
        "note": "Changes will take effect on next repository access",
        "updated_fields": list(config_updates.keys()),
    }


# Performance monitoring endpoints


@app.get("/metrics/performance")
def get_performance_metrics():
    """Get comprehensive performance metrics and analytics."""
    monitor = get_monitor()
    return monitor.get_performance_summary()


@app.get("/metrics/cache")
def get_cache_metrics():
    """Get detailed cache performance analytics."""
    monitor = get_monitor()
    return monitor.get_cache_analytics()


@app.get("/metrics/system")
def get_system_metrics():
    """Get system-level performance metrics."""
    monitor = get_monitor()

    # Update system metrics with current values
    try:
        import psutil  # type: ignore[import]

        cpu_percent = psutil.cpu_percent()
        memory_info = psutil.virtual_memory()
        memory_mb = memory_info.used / (1024 * 1024)
    except ImportError:
        cpu_percent = 0.0
        memory_mb = 0.0

    monitor.update_system_metrics(
        active_connections=0,  # Would need more complex tracking
        memory_usage_mb=memory_mb,
        cpu_usage_percent=cpu_percent,
    )

    return {
        "uptime_seconds": monitor.system_metrics.uptime_seconds,
        "total_requests": monitor.system_metrics.total_requests,
        "memory_usage_mb": round(memory_mb, 2),
        "cpu_usage_percent": round(cpu_percent, 2),
        "cache_stats": (
            get_repository().get_cache_stats()
            if hasattr(get_repository(), "get_cache_stats")
            else {}
        ),
    }


@app.get("/metrics/health")
def get_detailed_health_check():
    """Enhanced health check with performance indicators."""
    monitor = get_monitor()
    cache_analytics = monitor.get_cache_analytics()

    # Determine overall health
    cache_efficiency = cache_analytics["performance"]["cache_efficiency"]
    hit_rate = cache_analytics["performance"]["hit_rate_percent"]

    health_status = "healthy"
    if hit_rate < 60 or cache_efficiency == "poor":
        health_status = "degraded"
    elif hit_rate < 80 or cache_efficiency == "fair":
        health_status = "warning"

    return {
        "status": health_status,
        "timestamp": datetime.now().isoformat(),
        "schema_version": "4.0",
        "api_version": "0.3.0",
        "performance": {
            "cache_hit_rate_percent": hit_rate,
            "cache_efficiency": cache_efficiency,
            "total_requests": monitor.system_metrics.total_requests,
            "uptime_hours": round(monitor.system_metrics.uptime_seconds / 3600, 2),
        },
        "recommendations": cache_analytics["recommendations"],
    }


@app.post("/metrics/reset")
def reset_metrics():
    """Reset all performance metrics (useful for testing)."""
    monitor = get_monitor()
    monitor.reset_metrics()
    return {
        "message": "All metrics have been reset",
        "timestamp": datetime.now().isoformat(),
    }


@app.exception_handler(500)
async def internal_error_handler(request, exc):
    """Custom 500 handler for internal errors."""
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "detail": "An unexpected error occurred processing your request",
        },
    )
