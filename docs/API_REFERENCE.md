# HPXML Schema API – Detailed Reference

This document provides a structured reference for all public HTTP and GraphQL endpoints, headers, error shapes, caching semantics (ETag), and versioning behavior.

> For a quick high‑level overview, see the README. This file is meant for implementers writing clients, SDKs, or integrations.

---
## Conventions
- Base URL examples assume: `http://localhost:8000`
- All responses are JSON unless otherwise stated.
- Timestamps: ISO 8601 UTC unless endpoint-specific.
- Errors follow a normalized structure (see Error Format section).

---
## HTTP Endpoints

### 1. Health
`GET /health`

Simple readiness probe.

Response 200:
```json
{
  "status": "healthy",
  "schema_version": "4.0"
}
```

May include `error` field if degraded.

---
### 2. Metadata
`GET /metadata`

Returns schema provenance + parser context.

Headers Returned:
- `ETag`: Stable hash derived from schema source + parser config.
- `Last-Modified`: Timestamp of last schema load.
- `Cache-Control`: `public, max-age=3600`

Query Params: none.

Response 200 (fields may vary):
```json
{
  "schema_version": "4.0",
  "source": "/path/to/HPXML.xsd",
  "generated_at": "2025-09-17T12:34:56.123456",
  "parser_mode": "cached",
  "parser_config": {...}
}
```

Conditional Request Example:
```bash
etag=$(curl -sI /metadata | awk -F '"' '/ETag/ {print $2}')
curl -i -H "If-None-Match: \"$etag\"" /metadata  # 304 if unchanged
```

304 Response: empty body (may be `{}`) + preserved caching headers.

---
### 3. Tree
`GET /tree`

Returns the schema node hierarchy or a subtree.

Parameters:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `section` | string | no | Root xpath of subtree; omit for full tree root |
| `depth` | int (1–10) | no | Maximum depth (prunes deeper children) |

Headers: Supports `If-None-Match` using same `ETag` as `/metadata` (tree and metadata share schema ETag).

Response 200:
```json
{
  "node": {
    "xpath": "/HPXML",
    "name": "HPXML",
    "kind": "section",
    "children": [ ... ]
  }
}
```

404:
```json
{
  "error": "Not Found",
  "detail": "Section not found: /HPXML/Nope",
  "path": "/tree"
}
```

---
### 4. Fields
`GET /fields?section=/HPXML/Building/BuildingDetails`

Lists immediate field children and nested sections of a given node.

Response 200:
```json
{
  "section": {"xpath": "...", "name": "..."},
  "fields": [ {"name": "WallArea", "data_type": "decimal", ...} ],
  "children": [ {"name": "Walls", "kind": "section"}, ... ]
}
```

---
### 5. Search
`GET /search`

Parameters:
| Param | Required | Notes |
|-------|----------|-------|
| `query` | yes | Case-insensitive substring match (min length 2) |
| `kind` | no | Filter (`field`, `section`, `choice`) |
| `limit` | no (default 100) | Max 500 |

Response 200:
```json
{
  "results": [ {"xpath": "/HPXML/...", "name": "WallArea", "kind": "field", "data_type": "decimal"} ],
  "total": 1,
  "limited": false
}
```

---
### 6. Validate (Single)
`POST /validate`

Body:
```json
{ "xpath": "/HPXML/Building/.../Area", "value": "250" }
```

Response 200:
```json
{
  "valid": true,
  "errors": [],
  "warnings": []
}
```

Unknown path example:
```json
{
  "valid": false,
  "errors": ["Unknown xpath: /HPXML/Bad"],
  "warnings": []
}
```

---
### 7. Bulk Validate
`POST /validate/bulk`

Body:
```json
{
  "validations": [
    {"xpath": "/HPXML/.../Area", "value": "-10"},
    {"xpath": "/HPXML/.../Type", "value": "vented"}
  ]
}
```

Response 200 (abbreviated):
```json
{
  "results": [ {"valid": false, "errors": ["Value '-10' ..."], "warnings": []}, ... ],
  "summary": {"total": 2, "valid": 1, "invalid": 1, "errors": 1, "warnings": 0}
}
```

---
### 8. Parser Config (GET /config/parser)
Returns current parser configuration.

### 9. Update Parser Config (POST /config/parser)
Accepts partial overrides; takes effect on next repo access (cache is cleared).

Body example:
```json
{"max_recursion_depth": 30}
```

Response:
```json
{"message": "Parser configuration updated", "updated_fields": ["max_recursion_depth"]}
```

---
### 10. Metrics
- `GET /metrics/performance` – aggregate latency & counts
- `GET /metrics/cache` – hit/miss stats
- `GET /metrics/system` – memory & CPU snapshot
- `GET /metrics/health` – synthesized health classification
- `POST /metrics/reset` – reset counters

---
## Headers & Caching
| Header | Provided By | Purpose |
|--------|-------------|---------|
| `ETag` | `/metadata`, `/tree` | Content revalidation |
| `Last-Modified` | `/metadata` | Informational timestamp |
| `Cache-Control` | `/metadata`, `/tree` | Public caching hint |
| `X-Response-Time` | All | Request latency (seconds) |
| `X-API-Version` | All | Service version string |

Conditional flow: clients SHOULD store `ETag` and reissue conditional GETs. 304 responses SHOULD be treated as cache hits.

---
## Error Format
Generic examples:
```json
{
  "error": "Not Found",
  "detail": "Section not found: /HPXML/Bad",
  "path": "/tree"
}
```
500 handler:
```json
{"error": "Internal Server Error", "detail": "An unexpected error occurred processing your request"}
```
Validation endpoint errors are embedded inside the success (200) payload.

---
## Versioning Strategy
Current implementation exposes a canonical (cached) schema. Multi-version routing (e.g. `/v4.0/`, `/v4.1/`) can be layered via `versioned_routes.py`. The repository auto-detects schema version from the XSD when available; otherwise defaults to `4.0`.

Future changes should:
- Introduce explicit version prefix for breaking structural changes
- Maintain stable response fields across minor schema updates

---
## GraphQL Overview
`POST /graphql` (GraphiQL IDE available at `/graphql`)

Key root fields (placeholders may return minimal data until fully wired):
- `health: String`
- `metadata: SchemaMetadata`
- `tree(section, depth): RuleNode`
- `search(query, kind, limit, offset): [SearchResult]`
- `performanceMetrics: PerformanceMetrics`
- `cacheMetrics: CacheMetrics`

Mutations:
- `validateField(input)` – single field validation (placeholder logic)
- `validateBulk(inputs)` – multiple validations
- `resetMetrics` – resets performance monitor

Schema Export:
```bash
python scripts/export_schemas.py --out-dir build/schemas
cat build/schemas/graphql_schema.graphql
```

---
## MCP (Model Context Protocol) Resources
(If installed with `mcp` extra.)
- `metadata`, `tree`, `fields`, `search`, `health`, `performance_metrics`, `cache_metrics`
- Tools: `validate_field`, `validate_bulk`, `reset_metrics`

Use cases: editor integration, LLM semantic navigation, automated refactoring assistance.

---
## Rate Limiting & Security (Not Implemented Yet)
Recommended for production:
- Authentication (API key / OAuth2)
- Request size & rate limits on `/validate/bulk` & `/tree`
- Pagination for `search`
- Structured audit logging

---
## Change Detection & Schema Artifacts
Export OpenAPI & GraphQL specs, diff in CI:
```bash
python scripts/export_schemas.py --out-dir build/schemas
# Optionally fail build if drift detected
git diff --exit-code build/schemas/openapi.json
```

---
## Minimal Client Example
```python
import requests

base = "http://localhost:8000"
meta = requests.get(f"{base}/metadata").json()
print("Schema:", meta.get("schema_version"))

validate = requests.post(f"{base}/validate", json={
    "xpath": "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall/WallArea",
    "value": "250"
}).json()
print("Valid?", validate["valid"], validate["errors"]) 
```

---
## Test Coverage Reference
Associated contract tests:
- `test_etag_caching.py` – ETag + conditional GET
- `test_tree_depth_and_not_found.py` – subtree & 404 behavior
- `test_schema_exports.py` – schema artifact generation
- `test_version_matrix.py` – simulated multi-version handling

---
## Feedback
Open issues for clarifications or missing fields. PRs welcome for:
- Adding fully wired GraphQL resolvers
- Expanding validation semantics (cross-field)
- Pagination & filtering improvements

---
*End of API reference.*
