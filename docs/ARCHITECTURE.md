# HPXML Schema API Architecture

This document provides a high-level overview of the internal components that make up the HPXML Schema API service and how they interact.

## Goals

* Provide fast, cache-friendly access to HPXML rule / schema metadata.
* Support lightweight value validation for HPXML element/attribute candidates.
* Expose a REST + GraphQL surface with consistent performance monitoring.
* Offer an MCP (Model Context Protocol) bridge for tool integration.

## Component Map

```
+------------------+       +-----------------+       +------------------+
|  FastAPI Routes  | <---> |  RulesRepository | <---> | CachedSchemaParser |
| (REST + GraphQL) |       |  (app.py)       |       | (cache.py)         |
+------------------+       +-----------------+       +------------------+
        |                           |                         |
        |                           |                         v
        |                           |                  XSD / Schematron
        |                           |                         |
        v                           |                         v
+------------------+       +-----------------+       +------------------+
| Performance /    | <---- |  Monitoring     | <---- |  Cache Backends   |
| System Metrics   |       |  (monitoring.py)|       | (redis/fakeredis) |
+------------------+       +-----------------+       +------------------+
        ^                           |
        |                           v
        |                   +---------------+
        |                   |  MCP Server   |
        |                   | (mcp_server)  |
        |                   +---------------+
```

## Key Modules

### `app.py`
Defines FastAPI application, REST endpoints, dependency wiring, and `RulesRepository`. Adds request monitoring middleware and custom error handlers.

### `cache.py`
Implements caching strategies:
* `SchemaCache`: local in-process TTL cache for parsed rule nodes / fragments.
* `DistributedCache`: Redis (or fakeredis) backed cache with serialization (pickle primary, JSON/string fallback) + metadata side channel.
* `CachedSchemaParser`: Thin wrapper combining parsing + caching semantics used by the repository.

### `xsd_parser.py`
Parses the HPXML XSD into an internal tree of `RuleNode` objects capturing structure, datatypes, enumerations, and validations (Schematron-derived rules sourced through `schematron_parser.py`).

### `schematron_parser.py`
Extracts Schematron rules (messages, tests, contexts) and maps them onto corresponding rule nodes for advisory validation.

### `merger.py`
Responsible for merging extension or external rule fragments into the canonical tree (future expansion).

### `models.py`
Pydantic / dataclass style models describing rule nodes (`RuleNode`) and validation rules (`ValidationRule`). Used across REST, GraphQL, and MCP layers.

### `graphql_schema.py`
Strawberry schema defining types that mirror internal models. Some resolvers are placeholders pending full data wiring but still integrate with performance metrics.

### `monitoring.py`
`PerformanceMonitor` gathers endpoint timings, cache statistics, and system metrics; provides analytics endpoints (`/metrics/*`).

### `versioned_routes.py`
Adds versioned route prefixes if future breaking changes require side-by-side endpoint versions.

### `mcp_server.py` / `mcp_fastapi_integration.py`
Expose the repository via Model Context Protocol primitives so external tools (editors, agents) can query HPXML schema knowledge contextually.

## Data Flow (Typical Request)
1. Client calls `/tree` with optional `section` & `depth`.
2. Dependency `get_repository()` returns (cached, singleton) `RulesRepository`.
3. Repository consults `CachedSchemaParser` – which may fetch from local or distributed cache, otherwise triggers parse of XSD and attaches metadata.
4. Response serialized; middleware records timing & adds response headers (`X-Response-Time`, `X-API-Version`).
5. Performance monitor aggregates metrics; cache layer updates hit/miss counters.

## Caching Strategy
* Keys are namespaced with environment-configurable prefixes when using Redis.
* Values serialized with pickle, falling back to JSON => string for robustness.
* Metadata companion keys (`:meta`) store file mtime & etag for staleness checks.
* Local mirror ensures reads succeed even if Redis transiently unavailable.

## ETag & Conditional Requests
`/metadata` and `/tree` return stable ETags derived from parser configuration + schema source. Clients should revalidate using `If-None-Match` to reduce payload transfer when unchanged.

## Validation Scope
The repository's `validate_value` performs inexpensive structural checks:
* Unknown path detection
* Required field enforcement
* Enumeration membership
* Primitive datatype validation (int/float/boolean/date)
* Schematron rules surfaced as warnings (non-blocking)

Full document-level validation (cross-field dependencies) is intentionally out of scope for performance reasons.

## GraphQL Considerations
Currently returns mostly placeholder structures where backend integration is pending. Depth limiting (10) applied; per-resolver timing still recorded. Roadmap includes real tree traversal, search, and bulk validation parity.

## Environment Configuration
| Variable | Purpose | Default |
|----------|---------|---------|
| `HPXML_PARSER_MODE` | Parser mode (only `cached` honored) | `cached` |
| `HPXML_PARSER_CONFIG` | Comma-separated key=value overrides for parser config | (empty) |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `HPXML_FORCE_FAKEREDIS` | Force in-memory fakeredis backend | unset |
| `HPXML_CACHE_TTL` | Default TTL for cache entries (seconds) | 3600 |

## Observability
Metrics endpoints:
* `/metrics/performance` – latency, counts, error rate
* `/metrics/cache` – hit/miss, size, efficiency heuristics
* `/metrics/system` – memory, cpu, uptime, merged cache stats
* `/metrics/health` – synthesized health classification + recommendations

## MCP Integration
The MCP server exposes a subset of repository operations as structured tools (e.g., fetch metadata, list sections). This enables AI or editor integrations to retrieve context programmatically without bespoke REST calls.

## Future Enhancements
* Real GraphQL resolvers wired to repository
* OpenAPI & GraphQL schema export automation in CI
* Persistent metrics backend (Prometheus exporter)
* Fine-grained cache invalidation / warming strategies
* Multi-version schema negotiation

---
Questions or suggestions? Open an issue or see `CONTRIBUTING.md` for guidelines.
