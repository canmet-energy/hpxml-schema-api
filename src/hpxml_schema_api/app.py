"""FastAPI application exposing HPXML rules metadata."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .models import RuleNode
from .cache import get_cached_parser, CachedSchemaParser
from .monitoring import get_monitor
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
            stacklevel=2
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
    description="API for accessing HPXML schema rules and metadata with performance monitoring",
    docs_url="/docs",
    redoc_url="/redoc",
)


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
    context: Optional[Dict[str, str]] = Field(None, description="Additional context for validation")


class ValidationResponse(BaseModel):
    """Response model for validation endpoint."""
    valid: bool = Field(..., description="Whether the value is valid")
    errors: List[str] = Field(default_factory=list, description="List of validation errors")
    warnings: List[str] = Field(default_factory=list, description="List of validation warnings")


class BulkValidationRequest(BaseModel):
    """Request model for bulk validation endpoint."""
    validations: List[ValidationRequest] = Field(..., description="List of validation requests")


class BulkValidationResponse(BaseModel):
    """Response model for bulk validation endpoint."""
    results: List[ValidationResponse] = Field(..., description="List of validation results")
    summary: Dict[str, int] = Field(..., description="Summary statistics")


class ParserConfigRequest(BaseModel):
    """Request model for parser configuration."""
    max_extension_depth: Optional[int] = Field(None, ge=1, le=20, description="Maximum extension depth")
    max_recursion_depth: Optional[int] = Field(None, ge=5, le=50, description="Maximum recursion depth")
    track_extension_metadata: Optional[bool] = Field(None, description="Track extension metadata")
    resolve_extension_refs: Optional[bool] = Field(None, description="Resolve extension references")
    cache_resolved_refs: Optional[bool] = Field(None, description="Cache resolved references")


class ParserConfigResponse(BaseModel):
    """Response model for parser configuration."""
    max_extension_depth: int = Field(..., description="Current max extension depth")
    max_recursion_depth: int = Field(..., description="Current max recursion depth")
    track_extension_metadata: bool = Field(..., description="Current extension metadata tracking")
    resolve_extension_refs: bool = Field(..., description="Current extension refs resolution")
    cache_resolved_refs: bool = Field(..., description="Current cache resolved refs")


class RulesRepository:
    """Provide access to rule metadata using cached parser."""

    def __init__(self, mode: str = "cached", rules_path: Optional[Path] = None,
                 parser_config: Optional[ParserConfig] = None) -> None:
        self.mode = "cached"  # Always use cached mode
        self.parser_config = parser_config or ParserConfig()

        # Use cached parser
        self.cached_parser = get_cached_parser(
            str(self.parser_config.__dict__) if parser_config else None
        )
        # Initialize cached mode
        self._init_cached_mode()

    def _init_cached_mode(self) -> None:
        """Initialize cached parser mode."""
        try:
            # Try to discover HPXML schema automatically
            xsd_path = self._discover_hpxml_schema()
            if xsd_path:
                self.root = self.cached_parser.parse_xsd(xsd_path, "HPXML")
                self.metadata = {
                    "schema_version": "4.0",
                    "source": str(xsd_path),
                    "generated_at": datetime.now().isoformat(),
                    "parser_mode": "cached",
                    "parser_config": self.parser_config.__dict__,
                }
            else:
                raise FileNotFoundError("No HPXML schema found")
        except Exception:
            # Create minimal fallback root for testing/development
            self.root = RuleNode(
                name="HPXML",
                xpath="/HPXML",
                kind="section",
                description="HPXML root element (cached parser mode - fallback)"
            )
            self.metadata = {
                "schema_version": "4.0",
                "source": "fallback",
                "generated_at": datetime.now().isoformat(),
                "parser_mode": "cached",
                "parser_config": self.parser_config.__dict__,
            }

        # Calculate ETag from config and schema
        self._calculate_cached_etag()

    def _discover_hpxml_schema(self) -> Optional[Path]:
        """Discover HPXML schema file automatically."""
        # Common OpenStudio-HPXML locations
        potential_paths = [
            Path.home() / ".local/share/OpenStudio-HPXML-v1.9.1/HPXMLtoOpenStudio/resources/hpxml_schema/HPXML.xsd",
            Path("/usr/local/openstudio/HPXMLtoOpenStudio/resources/hpxml_schema/HPXML.xsd"),
            Path("/opt/openstudio/HPXMLtoOpenStudio/resources/hpxml_schema/HPXML.xsd"),
        ]

        for path in potential_paths:
            if path.exists():
                return path

        return None

    def _calculate_cached_etag(self) -> None:
        """Calculate ETag for cached mode."""
        config_str = str(self.parser_config.__dict__)
        content = f"{config_str}-{self.metadata.get('source', '')}"
        self.etag = f'"{hashlib.md5(content.encode()).hexdigest()}"'
        self.last_modified = datetime.now()

    def find(self, xpath: str) -> Optional[RuleNode]:
        """Return the rule node for the supplied HPXML xpath."""
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

    def validate_value(self, xpath: str, value: Optional[str] = None) -> ValidationResponse:
        """Validate a value against the rules for a given xpath."""
        node = self.find(xpath)
        if node is None:
            return ValidationResponse(
                valid=False,
                errors=[f"Unknown xpath: {xpath}"]
            )

        errors = []
        warnings = []

        # Check required field
        if node.min_occurs and node.min_occurs > 0 and not value:
            errors.append(f"Field '{node.name}' is required")

        # Check enumerations
        if value and node.enum_values:
            if value not in node.enum_values:
                errors.append(f"Value '{value}' not in allowed values: {', '.join(node.enum_values)}")

        # Check data type
        if value and node.data_type:
            if not self._validate_type(value, node.data_type):
                errors.append(f"Value '{value}' does not match expected type '{node.data_type}'")

        # Add any schematron validations as warnings
        if node.validations:
            for validation in node.validations:
                if validation.severity == "warning":
                    warnings.append(validation.message)

        return ValidationResponse(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )

    def _validate_type(self, value: str, data_type: str) -> bool:
        """Validate value against data type."""
        if data_type in ["integer", "positiveInteger"]:
            try:
                int_val = int(value)
                return int_val > 0 if data_type == "positiveInteger" else True
            except ValueError:
                return False
        elif data_type in ["decimal", "double"]:
            try:
                float(value)
                return True
            except ValueError:
                return False
        elif data_type == "boolean":
            return value.lower() in ["true", "false", "1", "0"]
        elif data_type == "date":
            try:
                datetime.strptime(value, "%Y-%m-%d")
                return True
            except ValueError:
                return False
        return True  # Default to valid for unknown types

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics if using cached mode."""
        if self.mode == "cached" and hasattr(self, 'cached_parser'):
            if hasattr(self.cached_parser, 'cache'):
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
        return {
            "status": "unhealthy",
            "error": str(e)
        }


@app.get("/metadata")
def metadata(
    response: Response,
    if_none_match: Optional[str] = Header(None),
    repo: RulesRepository = Depends(get_repository),
) -> Dict[str, object]:
    """Get metadata about the loaded schema rules."""
    # Check ETag
    if if_none_match and if_none_match == repo.etag:
        response.status_code = 304
        return {}

    # Set cache headers
    response.headers["ETag"] = repo.etag
    response.headers["Last-Modified"] = repo.last_modified.strftime("%a, %d %b %Y %H:%M:%S GMT")
    response.headers["Cache-Control"] = "public, max-age=3600"

    return repo.metadata


@app.get("/tree")
def tree(
    section: Optional[str] = Query(None, description="HPXML xpath to load"),
    depth: Optional[int] = Query(None, ge=1, le=10, description="Maximum depth to traverse"),
    response: Response = None,
    if_none_match: Optional[str] = Header(None),
    repo: RulesRepository = Depends(get_repository),
) -> Dict[str, object]:
    """Get the rule tree starting from a given section."""
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
    node = repo.find(section)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Section not found: {section}")
    field_nodes = [child.to_dict() for child in node.children if child.kind == "field"]
    child_sections = [child.to_dict() for child in node.children if child.kind != "field"]
    return {
        "section": node.to_dict(),
        "fields": field_nodes,
        "children": child_sections,
    }


@app.get("/search")
def search(
    query: str = Query(..., min_length=2, description="Case-insensitive label contains search"),
    kind: Optional[str] = Query(None, description="Filter by node kind (field, section, choice)"),
    limit: Optional[int] = Query(100, ge=1, le=500, description="Maximum number of results"),
    repo: RulesRepository = Depends(get_repository),
) -> Dict[str, object]:
    """Search for nodes by name."""
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
            matches.append({
                "xpath": node.xpath,
                "name": node.name,
                "kind": node.kind,
                "data_type": node.data_type,
            })

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
    """Validate a value against the rules for a given xpath."""
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
            "detail": str(exc.detail) if hasattr(exc, "detail") else "The requested resource was not found",
            "path": str(request.url.path),
        },
    )


@app.post("/validate/bulk")
def validate_bulk(
    request: BulkValidationRequest,
    repo: RulesRepository = Depends(get_repository),
) -> BulkValidationResponse:
    """Validate multiple values in a single request."""
    results = []
    summary = {"total": len(request.validations), "valid": 0, "invalid": 0, "errors": 0, "warnings": 0}

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
    if hasattr(repo, 'parser_config'):
        config_obj = repo.parser_config
    else:
        config_obj = PARSER_CONFIG

    return ParserConfigResponse(
        max_extension_depth=config_obj.max_extension_depth,
        max_recursion_depth=config_obj.max_recursion_depth,
        track_extension_metadata=config_obj.track_extension_metadata,
        resolve_extension_refs=config_obj.resolve_extension_refs,
        cache_resolved_refs=config_obj.cache_resolved_refs
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
        config_updates["track_extension_metadata"] = config_request.track_extension_metadata
    if config_request.resolve_extension_refs is not None:
        config_updates["resolve_extension_refs"] = config_request.resolve_extension_refs
    if config_request.cache_resolved_refs is not None:
        config_updates["cache_resolved_refs"] = config_request.cache_resolved_refs

    # Clear the repository cache to force recreation with new config
    get_repository.cache_clear()

    return {
        "message": "Parser configuration updated",
        "note": "Changes will take effect on next repository access",
        "updated_fields": list(config_updates.keys())
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
        import psutil
        cpu_percent = psutil.cpu_percent()
        memory_info = psutil.virtual_memory()
        memory_mb = memory_info.used / (1024 * 1024)
    except ImportError:
        cpu_percent = 0.0
        memory_mb = 0.0

    monitor.update_system_metrics(
        active_connections=0,  # Would need more complex tracking
        memory_usage_mb=memory_mb,
        cpu_usage_percent=cpu_percent
    )

    return {
        "uptime_seconds": monitor.system_metrics.uptime_seconds,
        "total_requests": monitor.system_metrics.total_requests,
        "memory_usage_mb": round(memory_mb, 2),
        "cpu_usage_percent": round(cpu_percent, 2),
        "cache_stats": get_repository().get_cache_stats() if hasattr(get_repository(), 'get_cache_stats') else {}
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
            "uptime_hours": round(monitor.system_metrics.uptime_seconds / 3600, 2)
        },
        "recommendations": cache_analytics["recommendations"]
    }


@app.post("/metrics/reset")
def reset_metrics():
    """Reset all performance metrics (useful for testing)."""
    monitor = get_monitor()
    monitor.reset_metrics()
    return {"message": "All metrics have been reset", "timestamp": datetime.now().isoformat()}


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
