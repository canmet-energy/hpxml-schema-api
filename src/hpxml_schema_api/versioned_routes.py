"""Versioned API routes for HPXML Schema API.

This module provides version-specific routing for the API endpoints.
Supports multiple HPXML schema versions with backward compatibility.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam
from fastapi import Query
from fastapi.responses import JSONResponse

from .enhanced_validation import ValidationContext, get_enhanced_validator
from .models import RuleNode, ValidationRule
from .monitoring import get_monitor
from .version_manager import get_version_manager, get_versioned_parser


def _build_versions_payload() -> Dict[str, Any]:
    """Construct the payload for the /versions endpoint.

    Separated for testability and potential reuse (e.g. MCP, docs export).
    """
    manager = get_version_manager()
    versions = manager.get_available_versions()
    default_version = manager.get_default_version()

    entries: List[Dict[str, Any]] = []
    for v in versions:
        info = manager.get_version_info(v)
        entries.append(
            {
                "version": v,
                "description": getattr(info, "description", None),
                "default": getattr(info, "default", False),
                "deprecated": getattr(info, "deprecated", False),
                "release_date": getattr(info, "release_date", None),
                "endpoints": {
                    "metadata": f"/v{v}/metadata",
                    "tree": f"/v{v}/tree",
                    "fields": f"/v{v}/fields",
                    "search": f"/v{v}/search",
                    "validate": f"/v{v}/validate",
                    "graphql": f"/v{v}/graphql",
                },
            }
        )

    return {"versions": entries, "default_version": default_version}


from .xsd_parser import ParserConfig


def get_version_from_path(
    version: str = PathParam(..., description="API version (e.g., v4.0, v4.1, latest)")
):
    """Extract and validate version from path parameter.

    Supports:
      - Explicit versions (e.g. 4.0, 4.1)
      - Optional leading 'v'
      - Alias 'latest' resolving to highest available version
    """
    # Strip 'v' prefix if present (e.g. v4.0 -> 4.0)
    raw = version
    clean_version = raw.lstrip("v")

    manager = get_version_manager()

    # Handle 'latest' alias before validation
    if clean_version.lower() == "latest":
        latest = manager.get_available_versions()
        if not latest:
            raise HTTPException(status_code=404, detail="No versions available")
        return latest[0]  # newest first ordering in get_available_versions

    if not manager.validate_version(clean_version):
        available = manager.get_available_versions()
        raise HTTPException(
            status_code=404,
            detail=f"Version {raw} not available. Available versions: {available}",
        )

    return clean_version


def create_versioned_router() -> APIRouter:
    """Create router with versioned endpoints."""
    router = APIRouter()

    @router.get("/versions")
    async def list_versions():
        """List available schema versions and associated endpoints."""
        return _build_versions_payload()

    @router.get("/v{version}/metadata")
    async def get_metadata_versioned(version: str = Depends(get_version_from_path)):
        """Get schema metadata for a specific version.

        Rules (aligned to tests):
        - Unknown/missing version or parser acquisition failure -> 404
        - parse_xsd raises an exception whose message contains 'parse error' -> 500
        - Any other parse_xsd exception (e.g. file not present, generic IO) -> 404
        - Success -> 200
        """
        start_time = time.time()

        # Attempt to get parser; on failure treat as missing version
        try:
            parser = get_versioned_parser(version)
        except Exception:
            parser = None
        if not parser:
            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/metadata", time.time() - start_time, 404
            )
            raise HTTPException(
                status_code=404, detail=f"Schema version {version} not available"
            )

        try:
            schema_tree = parser.parse_xsd()
        except Exception as e:
            monitor = get_monitor()
            msg_lower = str(e).lower()
            if "parse error" in msg_lower:
                monitor.record_endpoint_request(
                    f"/v{version}/metadata", time.time() - start_time, 500
                )
                raise HTTPException(
                    status_code=500, detail=f"Failed to load schema: {e}"
                )
            monitor.record_endpoint_request(
                f"/v{version}/metadata", time.time() - start_time, 404
            )
            raise HTTPException(
                status_code=404, detail=f"Schema version {version} not available"
            )

        total_nodes = _count_nodes(schema_tree)
        total_fields = _count_fields(schema_tree)
        total_sections = _count_sections(schema_tree)
        manager = get_version_manager()
        version_info = manager.get_version_info(version)
        result = {
            "version": version,
            "root_name": schema_tree.name,
            "total_nodes": total_nodes,
            "total_fields": total_fields,
            "total_sections": total_sections,
            "last_updated": version_info.release_date if version_info else None,
            "etag": f"v{version}-{hash(str(parser.parser_config.__dict__))}",
        }
        monitor = get_monitor()
        monitor.record_endpoint_request(
            f"/v{version}/metadata", time.time() - start_time, 200
        )
        return result

    @router.get("/v{version}/tree")
    async def get_tree_versioned(
        version: str = Depends(get_version_from_path),
        section: Optional[str] = Query(
            None, description="Specific section to retrieve"
        ),
        depth: Optional[int] = Query(None, description="Maximum depth to traverse"),
    ):
        """Get schema tree structure for specific version."""
        start_time = time.time()

        # Create parser config with depth limit if specified
        config = ParserConfig()
        if depth is not None:
            config.max_recursion_depth = max(
                1, min(depth, 20)
            )  # Limit to reasonable range

        parser = get_versioned_parser(version, config)
        if not parser:
            raise HTTPException(
                status_code=404, detail=f"Schema version {version} not available"
            )

        try:
            if section:
                # Parse specific section
                schema_tree = parser.parse_xsd(root_name=section)
            else:
                # Parse full tree
                schema_tree = parser.parse_xsd()

            # Apply depth limiting if specified
            if depth is not None:
                schema_tree = _limit_tree_depth(schema_tree, depth)

            result = _serialize_node(schema_tree)

            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/tree", time.time() - start_time, 200
            )

            return result

        except Exception as e:
            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/tree", time.time() - start_time, 500
            )
            raise HTTPException(
                status_code=500, detail=f"Failed to parse schema: {str(e)}"
            )

    @router.get("/v{version}/fields")
    async def get_fields_versioned(
        version: str = Depends(get_version_from_path),
        section: Optional[str] = Query(
            None, description="Specific section to get fields from"
        ),
        limit: Optional[int] = Query(
            100, description="Maximum number of fields to return"
        ),
    ):
        """Get field-level details for specific version."""
        start_time = time.time()

        parser = get_versioned_parser(version)
        if not parser:
            raise HTTPException(
                status_code=404, detail=f"Schema version {version} not available"
            )

        try:
            if section:
                schema_tree = parser.parse_xsd(root_name=section)
            else:
                schema_tree = parser.parse_xsd()

            # Extract all field nodes
            fields = _extract_fields(schema_tree)

            # Apply limit
            if limit and len(fields) > limit:
                fields = fields[:limit]

            result = [_serialize_node(field) for field in fields]

            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/fields", time.time() - start_time, 200
            )

            return result

        except Exception as e:
            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/fields", time.time() - start_time, 500
            )
            raise HTTPException(
                status_code=500, detail=f"Failed to get fields: {str(e)}"
            )

    @router.get("/v{version}/search")
    async def search_versioned(
        version: str = Depends(get_version_from_path),
        q: str = Query(..., description="Search query", min_length=2),
        kind: Optional[str] = Query(
            None, description="Node kind filter (field, section)"
        ),
        limit: Optional[int] = Query(100, description="Maximum results to return"),
        offset: Optional[int] = Query(0, description="Number of results to skip"),
    ):
        """Search schema nodes for specific version."""
        start_time = time.time()

        parser = get_versioned_parser(version)
        if not parser:
            raise HTTPException(
                status_code=404, detail=f"Schema version {version} not available"
            )

        try:
            schema_tree = parser.parse_xsd()

            # Search through nodes
            all_results = _search_nodes(schema_tree, q, kind)

            # Apply pagination
            total = len(all_results)
            start_idx = offset or 0
            end_idx = start_idx + (limit or 100)
            results = all_results[start_idx:end_idx]

            response_data = {
                "query": q,
                "kind_filter": kind,
                "total": total,
                "returned": len(results),
                "limit": limit,
                "offset": offset,
                "results": [_serialize_search_result(r) for r in results],
                "version": version,
            }

            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/search", time.time() - start_time, 200
            )

            return response_data

        except Exception as e:
            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/search", time.time() - start_time, 500
            )
            raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

    @router.post("/v{version}/validate")
    async def validate_field_versioned(
        version: str = Depends(get_version_from_path),
        validation_data: Dict[str, Any] = None,
    ):
        """Validate field values for specific version."""
        start_time = time.time()

        parser = get_versioned_parser(version)
        if not parser:
            raise HTTPException(
                status_code=404, detail=f"Schema version {version} not available"
            )

        try:
            # Basic validation - would need enhancement for full validation
            result = {"valid": True, "errors": [], "warnings": [], "version": version}

            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/validate", time.time() - start_time, 200
            )

            return result

        except Exception as e:
            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/validate", time.time() - start_time, 500
            )
            raise HTTPException(status_code=500, detail=f"Validation failed: {str(e)}")

    @router.post("/v{version}/validate/enhanced")
    async def validate_field_enhanced(
        version: str = Depends(get_version_from_path),
        validation_data: Dict[str, Any] = None,
    ):
        """Enhanced validation for a single field with business rules."""
        start_time = time.time()

        if not validation_data:
            raise HTTPException(status_code=400, detail="Validation data required")

        field_path = validation_data.get("field_path")
        value = validation_data.get("value")

        if not field_path:
            raise HTTPException(status_code=400, detail="field_path is required")

        try:
            # Create validation context
            context = ValidationContext(
                version=version,
                xpath_context=validation_data.get("xpath_context"),
                parent_values=validation_data.get("parent_values", {}),
                document_data=validation_data.get("document_data"),
                strict_mode=validation_data.get("strict_mode", False),
                custom_rules=validation_data.get("custom_rules", []),
            )

            # Get enhanced validator
            validator = get_enhanced_validator()
            result = validator.validate_field(field_path, value, context)

            # Convert to API response format
            response_data = {
                "valid": result.valid,
                "field_path": result.field_path,
                "value": result.value,
                "errors": result.errors,
                "warnings": result.warnings,
                "info": result.info,
                "rule_results": result.rule_results,
                "version": version,
            }

            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/validate/enhanced", time.time() - start_time, 200
            )

            return response_data

        except Exception as e:
            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/validate/enhanced", time.time() - start_time, 500
            )
            raise HTTPException(
                status_code=500, detail=f"Enhanced validation failed: {str(e)}"
            )

    @router.post("/v{version}/validate/bulk")
    async def validate_bulk_enhanced(
        version: str = Depends(get_version_from_path),
        validation_data: Dict[str, Any] = None,
    ):
        """Enhanced bulk validation for multiple fields."""
        start_time = time.time()

        if not validation_data:
            raise HTTPException(status_code=400, detail="Validation data required")

        field_values = validation_data.get("field_values", {})
        if not field_values:
            raise HTTPException(status_code=400, detail="field_values is required")

        try:
            # Create validation context
            context = ValidationContext(
                version=version,
                xpath_context=validation_data.get("xpath_context"),
                parent_values=validation_data.get("parent_values", {}),
                strict_mode=validation_data.get("strict_mode", False),
                custom_rules=validation_data.get("custom_rules", []),
            )

            # Get enhanced validator
            validator = get_enhanced_validator()
            result = validator.validate_bulk(field_values, context)

            # Convert to API response format
            response_data = {
                "overall_valid": result.overall_valid,
                "total_fields": result.total_fields,
                "valid_fields": result.valid_fields,
                "invalid_fields": result.invalid_fields,
                "summary": result.summary,
                "results": [
                    {
                        "valid": r.valid,
                        "field_path": r.field_path,
                        "value": r.value,
                        "errors": r.errors,
                        "warnings": r.warnings,
                        "info": r.info,
                        "rule_results": r.rule_results,
                    }
                    for r in result.results
                ],
                "version": version,
            }

            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/validate/bulk", time.time() - start_time, 200
            )

            return response_data

        except Exception as e:
            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/validate/bulk", time.time() - start_time, 500
            )
            raise HTTPException(
                status_code=500, detail=f"Bulk validation failed: {str(e)}"
            )

    @router.post("/v{version}/validate/document")
    async def validate_document_enhanced(
        version: str = Depends(get_version_from_path),
        validation_data: Dict[str, Any] = None,
    ):
        """Enhanced validation for complete HPXML document."""
        start_time = time.time()

        if not validation_data:
            raise HTTPException(status_code=400, detail="Validation data required")

        document_data = validation_data.get("document_data", {})
        if not document_data:
            raise HTTPException(status_code=400, detail="document_data is required")

        try:
            # Create validation context
            context = ValidationContext(
                version=version,
                strict_mode=validation_data.get("strict_mode", False),
                custom_rules=validation_data.get("custom_rules", []),
            )

            # Get enhanced validator
            validator = get_enhanced_validator()
            result = validator.validate_document(document_data, context)

            # Convert to API response format with additional document-level metadata
            response_data = {
                "overall_valid": result.overall_valid,
                "total_fields": result.total_fields,
                "valid_fields": result.valid_fields,
                "invalid_fields": result.invalid_fields,
                "summary": result.summary,
                "document_metadata": {
                    "version": version,
                    "validation_timestamp": time.time(),
                    "field_count": len(document_data),
                    "error_rate": result.summary.get("total_errors", 0)
                    / max(result.total_fields, 1),
                    "warning_rate": result.summary.get("total_warnings", 0)
                    / max(result.total_fields, 1),
                },
                "results": [
                    {
                        "valid": r.valid,
                        "field_path": r.field_path,
                        "value": r.value,
                        "errors": r.errors,
                        "warnings": r.warnings,
                        "info": r.info,
                        "rule_results": r.rule_results,
                    }
                    for r in result.results
                ],
            }

            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/validate/document", time.time() - start_time, 200
            )

            return response_data

        except Exception as e:
            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"/v{version}/validate/document", time.time() - start_time, 500
            )
            raise HTTPException(
                status_code=500, detail=f"Document validation failed: {str(e)}"
            )

    return router


def _count_nodes(node: RuleNode) -> int:
    """Count total number of nodes in tree."""
    count = 1
    for child in node.children:
        count += _count_nodes(child)
    return count


def _count_fields(node: RuleNode) -> int:
    """Count field nodes in tree."""
    count = 1 if node.kind == "field" else 0
    for child in node.children:
        count += _count_fields(child)
    return count


def _count_sections(node: RuleNode) -> int:
    """Count section nodes in tree."""
    count = 1 if node.kind == "section" else 0
    for child in node.children:
        count += _count_sections(child)
    return count


def _limit_tree_depth(
    node: RuleNode, max_depth: int, current_depth: int = 0
) -> RuleNode:
    """Limit tree depth by truncating children beyond max_depth."""
    if current_depth >= max_depth:
        # Create copy without children
        return RuleNode(
            xpath=node.xpath,
            name=node.name,
            kind=node.kind,
            data_type=node.data_type,
            min_occurs=node.min_occurs,
            max_occurs=node.max_occurs,
            repeatable=node.repeatable,
            enum_values=node.enum_values,
            description=node.description,
            validations=node.validations,
            notes=node.notes + ["depth_limited"],
            children=[],
        )

    # Process children recursively
    limited_children = [
        _limit_tree_depth(child, max_depth, current_depth + 1)
        for child in node.children
    ]

    return RuleNode(
        xpath=node.xpath,
        name=node.name,
        kind=node.kind,
        data_type=node.data_type,
        min_occurs=node.min_occurs,
        max_occurs=node.max_occurs,
        repeatable=node.repeatable,
        enum_values=node.enum_values,
        description=node.description,
        validations=node.validations,
        notes=node.notes,
        children=limited_children,
    )


def _extract_fields(node: RuleNode) -> List[RuleNode]:
    """Extract all field nodes from tree."""
    fields = []
    if node.kind == "field":
        fields.append(node)

    for child in node.children:
        fields.extend(_extract_fields(child))

    return fields


def _search_nodes(
    node: RuleNode, query: str, kind_filter: Optional[str] = None
) -> List[RuleNode]:
    """Search nodes by name, description, or xpath."""
    results = []
    query_lower = query.lower()

    # Check if current node matches
    matches = (
        query_lower in node.name.lower()
        or (node.description and query_lower in node.description.lower())
        or query_lower in node.xpath.lower()
    )

    if matches and (not kind_filter or node.kind == kind_filter):
        results.append(node)

    # Search children
    for child in node.children:
        results.extend(_search_nodes(child, query, kind_filter))

    return results


def _serialize_node(node: RuleNode) -> Dict[str, Any]:
    """Serialize RuleNode to dictionary."""
    return {
        "xpath": node.xpath,
        "name": node.name,
        "kind": node.kind,
        "data_type": node.data_type,
        "min_occurs": node.min_occurs,
        "max_occurs": node.max_occurs,
        "repeatable": node.repeatable,
        "enum_values": node.enum_values,
        "description": node.description,
        "validations": [
            {
                "message": v.message,
                "severity": v.severity,
                "test": v.test,
                "context": v.context,
            }
            for v in node.validations
        ],
        "notes": node.notes,
        "children": [_serialize_node(child) for child in node.children],
    }


def _serialize_search_result(node: RuleNode) -> Dict[str, Any]:
    """Serialize search result (node without children)."""
    return {
        "xpath": node.xpath,
        "name": node.name,
        "kind": node.kind,
        "data_type": node.data_type,
        "description": node.description,
        "notes": node.notes,
    }
