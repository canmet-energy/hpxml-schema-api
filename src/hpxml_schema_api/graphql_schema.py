"""GraphQL schema definition for the HPXML Schema API.

Overview
========
This module defines a Strawberry GraphQL schema that mirrors (or will mirror)
the REST surface of the HPXML Rules API. It focuses on *exploration* (tree,
search, metadata) and *lightweight validation* use cases while sharing the
same performance monitoring subsystem used by REST endpoints.

Current Implementation Status
-----------------------------
Several resolvers intentionally return placeholder data (particularly the
`tree`, `fields`, and `search` resolvers) pending full backend wiring to the
cached parser and merged ruleset. The schema is still published so that:

* The GraphQL ↔ MCP bridge can stabilize on a predictable contract.
* Client prototypes (CLI tools, notebooks, UI explorers) can be built early.
* Telemetry patterns (timing, endpoint naming) are validated in production.

Example Queries (GraphiQL / curl)
---------------------------------
Basic liveness and metadata::

        query {
            health
            metadata { version root_name total_nodes total_fields etag }
        }

Attempt a (currently empty) search::

        query {
            search(query: "Attic", limit: 5) { xpath name kind data_type }
        }

Example Mutations::

        mutation {
            validateField(input: { xpath: "/HPXML/Building/BuildingID", value: "B123" }) {
                valid
                errors
                warnings
            }
        }

        mutation {
            resetMetrics
        }

curl usage (JSON body)::

        curl -X POST http://localhost:8000/graphql \
                 -H "Content-Type: application/json" \
                 -d '{"query": "{ health metadata { version } }"}'

Performance & Monitoring
------------------------
Each resolver calls ``_record_graphql_metrics`` to record execution time in the
shared performance monitor. This keeps aggregated metrics (hit rates, latency
percentiles) coherent across REST and GraphQL surfaces.

Depth Limiting & Safety
-----------------------
``QueryDepthLimiter`` is applied with ``max_depth=10`` to mitigate risk of
pathologically deep queries. Future improvements may add complexity cost
estimation (node count / field multiplicity) before execution.

Design Notes / Roadmap
----------------------
* Replace placeholders with real parser-backed resolvers.
* Add pagination support to search (cursor or offset-based).
* Introduce a unified validation mutation that mirrors bulk REST endpoint.
* Surface cache statistics and repository metadata directly for parity with REST.
* Enforce input size limits before expensive resolver work.

Testing Guidance
----------------
Given placeholder implementations, unit tests should currently focus on:
* Schema instantiation (no import-time failures)
* Resolver call success (returns expected shape / placeholder values)
* Metrics side-effects (invocation increments request counters)

Once real data is wired, tests should expand to snapshot key structural
responses (e.g., a shallow tree query) while avoiding brittle full-tree
snapshots.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import strawberry
from strawberry.extensions import QueryDepthLimiter
from strawberry.fastapi import GraphQLRouter
from strawberry.types import Info

from .cache import get_cached_parser
from .merger import merge_rules
from .models import RuleNode as RuleNodeModel
from .models import ValidationRule as ValidationRuleModel
from .monitoring import get_monitor


@strawberry.type
class ValidationRule:
    """GraphQL representation of a Schematron/business validation rule."""

    message: str
    severity: str
    test: Optional[str] = None
    context: Optional[str] = None

    @classmethod
    def from_model(cls, rule: ValidationRuleModel) -> "ValidationRule":
        """Convert from model to GraphQL type."""
        return cls(
            message=rule.message,
            severity=rule.severity,
            test=rule.test,
            context=rule.context,
        )


@strawberry.type
class RuleNode:
    """GraphQL type mirroring internal :class:`RuleNodeModel`.

    Optional depth limiting in ``from_model`` helps mitigate very large trees
    during exploration queries.
    """

    xpath: str
    name: str
    kind: str
    data_type: Optional[str] = None
    min_occurs: Optional[int] = None
    max_occurs: Optional[str] = None
    repeatable: bool = False
    enum_values: List[str] = strawberry.field(default_factory=list)
    description: Optional[str] = None
    validations: List[ValidationRule] = strawberry.field(default_factory=list)
    notes: List[str] = strawberry.field(default_factory=list)
    children: List["RuleNode"] = strawberry.field(default_factory=list)

    @classmethod
    def from_model(
        cls,
        node: RuleNodeModel,
        max_depth: Optional[int] = None,
        current_depth: int = 0,
    ) -> "RuleNode":
        """Convert from model to GraphQL type with optional depth limiting."""
        # Convert validations
        validations = [ValidationRule.from_model(rule) for rule in node.validations]

        # Convert children with depth limiting
        children = []
        if max_depth is None or current_depth < max_depth:
            children = [
                cls.from_model(child, max_depth, current_depth + 1)
                for child in node.children
            ]

        return cls(
            xpath=node.xpath,
            name=node.name,
            kind=node.kind,
            data_type=node.data_type,
            min_occurs=node.min_occurs,
            max_occurs=node.max_occurs,
            repeatable=node.repeatable,
            enum_values=node.enum_values or [],
            description=node.description,
            validations=validations,
            notes=node.notes or [],
            children=children,
        )


@strawberry.type
class SearchResult:
    """Flattened node representation used in search result lists."""

    xpath: str
    name: str
    kind: str
    data_type: Optional[str] = None
    description: Optional[str] = None
    notes: List[str] = strawberry.field(default_factory=list)

    @classmethod
    def from_model(cls, node: RuleNodeModel) -> "SearchResult":
        """Convert from model to GraphQL search result type."""
        return cls(
            xpath=node.xpath,
            name=node.name,
            kind=node.kind,
            data_type=node.data_type,
            description=node.description,
            notes=node.notes or [],
        )


@strawberry.type
class ValidationResult:
    """Outcome of validating one field value (simplified placeholder)."""

    valid: bool
    errors: List[str] = strawberry.field(default_factory=list)
    warnings: List[str] = strawberry.field(default_factory=list)


@strawberry.type
class SchemaMetadata:
    """High-level descriptive statistics about the loaded schema."""

    version: str
    root_name: str
    total_nodes: int
    total_fields: int
    total_sections: int
    last_updated: str
    etag: str


@strawberry.type
class PerformanceMetrics:
    """Aggregated performance metrics sourced from shared monitor."""

    # Use camelCase-style names to align with existing tests expecting these keys
    total_requests: int
    average_response_time: float
    fastest_response_time: float
    slowest_response_time: float
    error_rate: float
    endpoints: List[str]


@strawberry.type
class CacheMetrics:
    """Cache subsystem metrics (hits, misses, size, memory usage)."""

    cache_hits: int
    cache_misses: int
    hit_rate: float
    cache_size: int
    memory_usage_mb: float
    evictions: int


@strawberry.input
class ValidationInput:
    """Input payload for validation mutations."""

    xpath: str
    value: Optional[str] = None
    context: Optional[str] = None  # JSON string for context


@strawberry.enum
class NodeKind(Enum):
    """Enumeration of node structural kinds for filtering queries."""

    FIELD = "field"
    SECTION = "section"


def _get_parser():
    """Return the cached parser instance (placeholder connector)."""
    return get_cached_parser()


def _record_graphql_metrics(operation_name: str, start_time: float):
    """Record execution time for an operation in shared performance monitor."""
    response_time = time.time() - start_time
    monitor = get_monitor()
    endpoint = f"GraphQL {operation_name}"
    monitor.record_endpoint_request(endpoint, response_time, 200)


@strawberry.type
class Query:
    """Root query type exposing schema exploration operations."""

    @strawberry.field
    async def health(self) -> str:
        """Simple liveness probe returning "OK" when service is responsive."""
        start_time = time.time()
        result = "OK"
        _record_graphql_metrics("health", start_time)
        return result

    @strawberry.field
    async def metadata(self) -> SchemaMetadata:
        """Return basic schema metadata (placeholder content)."""
        start_time = time.time()

        parser = _get_parser()
        # This would need to be implemented to match the REST endpoint
        # For now, return basic metadata
        result = SchemaMetadata(
            version="4.0",
            root_name="HPXML",
            total_nodes=0,
            total_fields=0,
            total_sections=0,
            last_updated="2024-01-01T00:00:00Z",
            etag="sample-etag",
        )

        _record_graphql_metrics("metadata", start_time)
        return result

    @strawberry.field
    async def tree(
        self, section: Optional[str] = None, depth: Optional[int] = None
    ) -> Optional[RuleNode]:
        """Return (placeholder) root subtree; implementation pending."""
        start_time = time.time()

        parser = _get_parser()
        # This would need actual schema loading logic
        # For now, return a placeholder

        _record_graphql_metrics("tree", start_time)
        return None

    @strawberry.field
    async def fields(
        self, section: Optional[str] = None, limit: Optional[int] = 100
    ) -> List[RuleNode]:
        """Return a list of field nodes (placeholder implementation)."""
        start_time = time.time()

        parser = _get_parser()
        # This would need actual field loading logic
        result = []

        _record_graphql_metrics("fields", start_time)
        return result

    @strawberry.field
    async def search(
        self,
        query: str,
        kind: Optional[NodeKind] = None,
        limit: Optional[int] = 100,
        offset: Optional[int] = 0,
    ) -> List[SearchResult]:
        """Search for nodes (placeholder returns empty until implemented)."""
        start_time = time.time()

        if len(query) < 2:
            _record_graphql_metrics("search", start_time)
            return []

        parser = _get_parser()
        # This would need actual search logic
        result = []

        _record_graphql_metrics("search", start_time)
        return result

    @strawberry.field
    async def performance_metrics(self) -> PerformanceMetrics:
        """Expose current performance metrics snapshot from monitor."""
        start_time = time.time()

        monitor = get_monitor()
        summary = monitor.get_performance_summary()
        api_section = summary.get("api", {})
        total_requests = api_section.get("total_requests", 0)
        # Build endpoint list from top_endpoints plus ensure any GraphQL labeled endpoints are included
        top_endpoints = [
            e.get("endpoint") for e in api_section.get("top_endpoints", [])
        ]
        # Append any endpoint metrics names explicitly containing 'GraphQL'
        for name in monitor.endpoint_metrics.keys():  # type: ignore[attr-defined]
            if "GraphQL" in name and name not in top_endpoints:
                top_endpoints.append(name)

        # Derive simple latency stats (reuse average of top endpoints for now)
        avg_resp = 0.0
        fastest = 0.0
        slowest = 0.0
        if top_endpoints:
            times = []
            for ep in top_endpoints:
                m = monitor.endpoint_metrics.get(ep)  # type: ignore[attr-defined]
                if m and m.total_requests:
                    times.append(m.average_response_time)
            if times:
                avg_resp = sum(times) / len(times)
                fastest = min(times)
                slowest = max(times)

        result = PerformanceMetrics(
            total_requests=total_requests,
            average_response_time=avg_resp,
            fastest_response_time=fastest,
            slowest_response_time=slowest,
            error_rate=0.0,
            endpoints=top_endpoints,
        )

        _record_graphql_metrics("performance_metrics", start_time)
        return result

    @strawberry.field
    async def cache_metrics(self) -> CacheMetrics:
        """Expose current cache metrics snapshot from monitor."""
        start_time = time.time()

        monitor = get_monitor()
        cache_stats = monitor.get_cache_analytics()

        result = CacheMetrics(
            cache_hits=cache_stats.get("cache_hits", 0),
            cache_misses=cache_stats.get("cache_misses", 0),
            hit_rate=cache_stats.get("hit_rate", 0.0),
            cache_size=cache_stats.get("cache_size", 0),
            memory_usage_mb=cache_stats.get("memory_usage_mb", 0.0),
            evictions=cache_stats.get("evictions", 0),
        )

        _record_graphql_metrics("cache_metrics", start_time)
        return result


@strawberry.type
class Mutation:
    """Root mutation type for write / validation operations."""

    @strawberry.mutation
    async def validate_field(self, input: ValidationInput) -> ValidationResult:
        """Validate a single field value (placeholder always valid)."""
        start_time = time.time()

        # Basic validation logic - would need to be enhanced
        result = ValidationResult(valid=True, errors=[], warnings=[])

        _record_graphql_metrics("validate_field", start_time)
        return result

    @strawberry.mutation
    async def validate_bulk(
        self, inputs: List[ValidationInput]
    ) -> List[ValidationResult]:
        """Validate multiple field values (placeholder all valid)."""
        start_time = time.time()

        results = []
        for input_item in inputs:
            # Basic validation logic - would need to be enhanced
            result = ValidationResult(valid=True, errors=[], warnings=[])
            results.append(result)

        _record_graphql_metrics("validate_bulk", start_time)
        return results

    @strawberry.mutation
    async def reset_metrics(self) -> bool:
        """Reset the shared performance monitor state; returns True on success."""
        start_time = time.time()

        monitor = get_monitor()
        monitor.reset_metrics()

        _record_graphql_metrics("reset_metrics", start_time)
        return True


# Create the GraphQL schema with extensions
schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    extensions=[
        QueryDepthLimiter(max_depth=10),  # Limit query depth
    ],
)


# Create the GraphQL router with GraphiQL interface
graphql_router = GraphQLRouter(
    schema, graphql_ide="graphiql", path="/graphql"  # Enable GraphiQL interface
)
