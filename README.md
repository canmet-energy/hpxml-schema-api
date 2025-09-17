# HPXML Schema API

A lightweight REST + (placeholder) GraphQL service for HPXML schema exploration and basic field validation. Focus areas: fast schema tree access, simple value checks, caching, and exportable API/GraphQL schemas. Advanced “enhanced/document” validation endpoints exist under versioned routes but are experimental and may change.

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/tests-274%20passing-brightgreen.svg)](#testing)

## 🚀 Quick Start

### Installation

```bash
# Install from PyPI
pip install hpxml-schema-api

# Or install with uv
uv add hpxml-schema-api

# Or clone and install locally
git clone https://github.com/canmet-energy/hpxml-schema-api.git
cd hpxml-schema-api
pip install -e .
```

### Running the Server

```bash
# Start the API server
hpxml-schema-api

# Or run with uvicorn directly
uvicorn hpxml_schema_api.app:app --host 0.0.0.0 --port 8000

# Server will be available at http://localhost:8000
# API documentation at http://localhost:8000/docs
```

### Model Context Protocol (MCP) Support

The HPXML Schema API includes full Model Context Protocol support, making it accessible to LLMs like Claude for AI-assisted development workflows.

#### Installing with MCP Support

```bash
# Install with MCP dependencies
pip install hpxml-schema-api[mcp]

# Or with uv
uv add hpxml-schema-api --extra mcp
```

#### Running the MCP Server

```bash
# Start standalone MCP server (stdio transport)
hpxml-mcp-server

# Start MCP server with HTTP transport
MCP_TRANSPORT=http MCP_PORT=8001 hpxml-mcp-server

# Start with authentication
MCP_AUTH_TOKEN=your-token MCP_REQUIRE_AUTH=true hpxml-mcp-server
```

#### Claude Desktop Integration

1. **Install the HPXML Schema API with MCP support:**
   ```bash
   pip install hpxml-schema-api[mcp]
   ```

2. **Add to Claude Desktop configuration** (`~/.claude/claude_desktop_config.json`):
   ```json
   {
     "mcpServers": {
       "hpxml-schema": {
         "command": "hpxml-mcp-server",
         "env": {
           "HPXML_SCHEMA_PATH": "/path/to/HPXML.xsd"
         }
       }
     }
   }
   ```

3. **Restart Claude Desktop** and the HPXML Schema API will be available as an MCP server.

#### Claude Code Integration

**✅ Fully Tested and Ready for Use**

1. **Install the HPXML Schema API:**
   ```bash
   pip install hpxml-schema-api[mcp]
```
### Enhanced / Bulk / Document Validation (Experimental)

Versioned endpoints under `/vX.Y/validate/*` expose extended validation modes (enhanced field, bulk, document). These are experimental, subject to change, and not yet part of the stability contract. Use only if you are prepared to adapt to breaking changes.
   ```

2. **Add MCP server to Claude Code:**
Core unversioned endpoints target a single cached schema (default 4.0). Versioned routes (`/v4.0/...`, `/v4.1/...`) are available when multiple schemas are present; detection falls back to 4.0 if 4.1 is not found. A discovery endpoint `/versions` now lists all known versions plus their endpoint templates. An alias `latest` maps to the newest available version (e.g. `/vlatest/metadata`).
   # Quick setup (uses HPXML v4.0 default, auto-downloads schema)
   claude mcp add hpxml-schema-api --command hpxml-mcp-server
   ```

   **Advanced Configuration:**
   ```bash
   # Use HPXML v4.1 instead of default v4.0
   claude mcp add hpxml-schema-api --command hpxml-mcp-server --env HPXML_SCHEMA_VERSION=4.1

   # Use specific schema file path
   claude mcp add hpxml-schema-api --command hpxml-mcp-server --env HPXML_SCHEMA_PATH=/path/to/HPXML.xsd

   # Enable debug logging
   claude mcp add hpxml-schema-api --command hpxml-mcp-server --env MCP_LOG_LEVEL=DEBUG
   ```

   **What happens automatically:**
   - 📥 Downloads HPXML schema from official repository if needed
   - 💾 Caches schema locally for offline use
   - 🔄 Supports both HPXML v4.0 and v4.1 simultaneously
   - 🚀 Ready to use immediately after installation

### GraphQL Interface (Placeholder)
       "servers": {
         "hpxml-schema": {
           "command": "hpxml-mcp-server",
           "args": ["--transport", "stdio"],
           "env": {
             "HPXML_SCHEMA_PATH": "~/.local/share/OpenStudio-HPXML-v1.9.1/HPXMLtoOpenStudio/resources/hpxml_schema/HPXML.xsd",
             "MCP_LOG_LEVEL": "INFO"
           }
         }
       }
     }
   }
   ```

3. **Use in Claude Code:**
   ```
   # Schema exploration
   "What HPXML schema version are we using and what's available?"
   "Show me the structure of the HPXML Building section"
   "What fields are required for HPXML BuildingDetails?"

   # Field validation
   "Validate this BuildingID: 'MyHouse123'"
   "Check if these HPXML field values are valid: BuildingID=Test, Area=1500"

   # Development assistance
   "Generate a Python function to validate HPXML Building data"
   "Create a JSON schema for HPXML validation"
   "Help me understand HPXML field requirements"
   ```

  **Available MCP Resources (7):**
  (All standard resources use the `schema://` URI scheme; versions catalog uses a distinct scheme.)
  - 📋 `schema://metadata` - Schema version and statistics
  - 🌳 `schema://tree` - Hierarchical schema structure
  - 📝 `schema://fields` - Field-level details and types
  - 🔍 `schema://search` - Search across schema elements
  - ❤️ `schema://health` - Server health status
  - 📊 `schema://performance_metrics` - Performance analytics
  - 💾 `schema://cache_metrics` - Cache statistics

   **Available MCP Tools (3):**
   - ✅ `validate_field` - Validate individual field values
   - 📦 `validate_bulk` - Batch validation for multiple fields
   - 🔄 `reset_metrics` - Reset performance metrics

#### Testing and Verification

The MCP integration has been **fully tested** with Claude Code:

**✅ Verified Functionality:**
- Schema discovery and metadata retrieval
- Field validation and bulk validation
- Version switching (v4.0 ↔ v4.1)
- Automatic schema downloading
- Resource and tool enumeration

**🔧 Troubleshooting:**
```bash
# Check if MCP server is working
hpxml-schema list                    # Should show available versions
hpxml-mcp-server --help             # Should show MCP server options

# Test MCP server manually
echo '{"method": "ping", "params": {}}' | hpxml-mcp-server

# Clear schema cache if issues
hpxml-schema clear
hpxml-schema download --version 4.0  # Re-download default schema
```

## 🔭 Roadmap ( abridged )
- Wire GraphQL resolvers to real parser data
- Stabilize enhanced/bulk/document validation contracts
- Add version usage metrics & deprecation flags
- Prometheus metrics exporter (optional)
- Pagination & filtering improvements for search
- Async Redis backend (if concurrency demands)
- Document MCP versions resource
### Schema Management

The API supports both **HPXML v4.0** (default) and **v4.1**, automatically discovering and downloading schemas as needed:

### Schema Artifact Export (OpenAPI & GraphQL)

You can export static API artifacts for integration, documentation sites, or client SDK generation.

```bash
python scripts/export_schemas.py --out-dir build/schemas
ls build/schemas
# openapi.json  graphql_schema.graphql
```

Typical uses:
* Commit `openapi.json` for diffing in PRs
* Feed `graphql_schema.graphql` into client codegen tools
* Publish both to an internal developer portal

CI suggestion (GitHub Actions step):
```bash
python scripts/export_schemas.py --out-dir build/schemas
git diff --exit-code build/schemas/openapi.json
```

If you only need one artifact:
```bash
python -c "from hpxml_schema_api.app import app, json; import json,sys; print(json.dumps(app.openapi(),indent=2))" > openapi.json
python -c "from hpxml_schema_api.graphql_schema import schema; print(schema.as_str())" > graphql_schema.graphql
```

The export script has no side effects beyond file creation and does not require a running server.

```bash
# Auto-discover or download default schema (v4.0)
hpxml-schema discover

# List available schema versions (shows cached status)
hpxml-schema list

# Download specific version
hpxml-schema download --version 4.0  # Default version
hpxml-schema download --version 4.1  # Latest version

# Clear cached schemas
hpxml-schema clear
```

**Supported Versions:**
- **v4.0** - Default version, stable and widely used
- **v4.1** - Latest version with newest features
- **latest** - Development version from master branch

### Your First Requests

```bash
# Check API health
curl http://localhost:8000/health

# Get schema metadata
curl http://localhost:8000/metadata

# Explore schema tree structure
curl http://localhost:8000/tree?depth=2

# Search for specific fields
curl http://localhost:8000/search?q=Building&limit=5

# Validate a field value
curl -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"xpath": "/HPXML/Building/BuildingID", "value": "MyBuilding123"}'
```

## 📖 API Documentation

For a full, parameter-by-parameter reference (endpoints, headers, error shapes, caching semantics, and GraphQL fields) see: **[Detailed API Reference](./docs/API_REFERENCE.md)**.

The table below is a quick navigation aid—consult the reference for authoritative details and stability notes.

### Core Endpoints

| Endpoint | Method | Description | Example |
|----------|--------|-------------|---------|
| `/health` | GET | API health check | `curl /health` |
| `/metadata` | GET | Schema metadata and statistics | `curl /metadata` |
| `/tree` | GET | Hierarchical schema structure | `curl /tree?depth=3` |
| `/fields` | GET | Field-level details | `curl /fields?section=Building` |
| `/search` | GET | Search schema elements | `curl /search?q=heating&kind=field` |
| `/validate` | POST | Basic field validation | See examples below |

### Enhanced Validation

Enhanced validation provides business rule checking beyond basic schema validation:

```bash
# Enhanced single field validation with custom rules
curl -X POST http://localhost:8000/v4.0/validate/enhanced \
  -H "Content-Type: application/json" \
  -d '{
    "field_path": "/HPXML/Building/BuildingDetails/BuildingSummary/BuildingConstruction/ConditionedFloorArea",
    "value": "1500",
    "custom_rules": [
      {"type": "numeric_range", "min": 500, "max": 10000}
    ]
  }'

# Bulk validation with cross-field checks
curl -X POST http://localhost:8000/v4.0/validate/bulk \
  -H "Content-Type: application/json" \
  -d '{
    "field_values": {
      "/HPXML/Building/BuildingDetails/BuildingSummary/BuildingConstruction/ConditionedFloorArea": "1500",
      "/HPXML/Building/BuildingDetails/Systems/HVAC/HVACPlant/HeatingSystem/HeatingSystemType": "Furnace"
    }
  }'

# Complete document validation
curl -X POST http://localhost:8000/v4.0/validate/document \
  -H "Content-Type: application/json" \
  -d '{
    "document_data": {
      "/HPXML/Building/BuildingID": "MyBuilding123",
      "/HPXML/Building/BuildingDetails/BuildingSummary/BuildingConstruction/ConditionedFloorArea": "1500"
    },
    "strict_mode": true
  }'
```

### Version Support

The API supports multiple HPXML schema versions simultaneously:

```bash
# List available versions
curl http://localhost:8000/versions

# Use latest alias
curl http://localhost:8000/vlatest/metadata

# Default endpoints (use v4.0)
curl http://localhost:8000/metadata
curl http://localhost:8000/validate

# Version-specific endpoints
curl http://localhost:8000/v4.0/metadata    # Explicit v4.0
curl http://localhost:8000/v4.1/metadata    # Latest v4.1
curl http://localhost:8000/v4.0/search?q=Building
curl http://localhost:8000/v4.1/search?q=Building

# Enhanced validation for specific versions
curl -X POST http://localhost:8000/v4.0/validate/enhanced \
  -d '{"field_path": "/HPXML/Building/BuildingID", "value": "MyBuilding"}'
curl -X POST http://localhost:8000/v4.1/validate/enhanced \
  -d '{"field_path": "/HPXML/Building/Area", "value": "1200"}'
```

**Version Selection:**
- **Unversioned endpoints** (`/metadata`, `/validate`) use **v4.0** by default
- **Versioned endpoints** (`/v4.0/`, `/v4.1/`, `/vlatest/`) use the specified or aliased version
- **Alias** `latest` resolves dynamically to the highest semantic version
- **MCP server** can be configured for specific versions via environment variables

### GraphQL Interface

Interactive GraphQL endpoint with GraphiQL interface:

```bash
# GraphQL endpoint
curl -X POST http://localhost:8000/graphql \
  -H "Content-Type: application/json" \
  -d '{
    "query": "{ metadata { version totalNodes totalFields } }"
  }'

# Access GraphiQL interface at http://localhost:8000/graphql
```

Example GraphQL queries:

```graphql
# Get schema metadata
{
  metadata {
    version
    rootName
    totalNodes
    totalFields
  }
}

# Search for fields
{
  search(query: "Building", limit: 5) {
    results {
      xpath
      name
      kind
      dataType
    }
  }
}

# Get tree structure with limited depth
{
  tree(depth: 2) {
    xpath
    name
    children {
      xpath
      name
      kind
    }
  }
}
```

### MCP Resources

When running with MCP support (`pip install hpxml-schema-api[mcp]`), the following resources are exposed to AI tooling:

| Resource URI | Purpose |
|--------------|---------|
| `schema://metadata` | Current schema version metadata (unversioned view) |
| `schema://tree` | Hierarchical schema structure (depth‑limited traversal) |
| `schema://fields` | Flat list of field nodes |
| `schema://search` | Search results for a query term |
| `schema://performance_metrics` | Recent performance stats |
| `schema://cache_metrics` | Cache usage statistics |
| `schema://health` | Health/status snapshot |
| `mcp://schema_versions` | Full version catalog with endpoint templates (includes `latest` alias resolution) |

You can retrieve the version catalog programmatically via MCP by reading `mcp://schema_versions` or through HTTP at `/versions`.

> MCP URI Conventions: `schema://*` resources map to internal schema exploration capabilities (metadata, tree, fields, search, metrics, health). These are not HTTP URLs; clients invoke them via the MCP `read_resource` method. The special `mcp://schema_versions` resource returns the aggregated version discovery payload equivalent to the REST `/versions` endpoint. All other functionality mirrors the REST interface but is optimized for AI tooling consumption.

## 🔧 Configuration

### Environment Variables

```bash
# Cache configuration
export HPXML_CACHE_TTL=3600              # Cache TTL in seconds (default: 3600)
export HPXML_CACHE_TYPE=distributed     # Cache type: local or distributed

# Redis configuration (for distributed cache)
export HPXML_REDIS_URL=redis://localhost:6379/0
export HPXML_REDIS_PREFIX=hpxml:

# Schema configuration
export HPXML_SCHEMA_DIR=/path/to/schemas # Directory with versioned schemas
export HPXML_SCHEMA_PATH=/path/to/HPXML.xsd  # Single schema file path

# Server configuration
export HPXML_HOST=0.0.0.0
export HPXML_PORT=8000
```

### Parser Configuration

```python
from hpxml_schema_api.xsd_parser import ParserConfig
from hpxml_schema_api.cache import get_cached_parser

# Create custom parser configuration
config = ParserConfig(
    max_extension_depth=5,          # Inheritance chain limit
    max_recursion_depth=15,         # Overall recursion limit
    track_extension_metadata=True,  # Extension chain indexing
    resolve_extension_refs=False,   # Resolve extension elements
    cache_resolved_refs=True        # Cache resolved references
)

# Use custom configuration
parser = get_cached_parser(
    parser_config_key="max_extension_depth=5,track_extension_metadata=true"
)
```

### Distributed Caching

For production deployments with multiple instances:

```python
from hpxml_schema_api.cache import DistributedCache, CachedSchemaParser

# Configure distributed cache
cache = DistributedCache(
    default_ttl=7200,
    redis_url="redis://localhost:6379/0",
    redis_prefix="myapp:",
    enable_monitoring=True
)

parser = CachedSchemaParser(cache=cache)
```

## 🎯 Use Cases

### Dynamic Form Generation

```python
import requests

# Get schema tree for form generation
response = requests.get("http://localhost:8000/tree", params={"depth": 3})
schema_tree = response.json()

# Get field details for specific section
response = requests.get("http://localhost:8000/fields",
                       params={"section": "Building"})
fields = response.json()

# Generate form fields from schema
for field in fields:
    if field["kind"] == "field":
        form_field = {
            "name": field["name"],
            "type": field["dataType"],
            "required": field.get("minOccurs", 0) > 0,
            "options": field.get("enumValues", []),
            "validation": field.get("validations", [])
        }
```

### Document Validation

```python
import requests

# Validate complete HPXML document
document_data = {
    "/HPXML/Building/BuildingID": "MyBuilding123",
    "/HPXML/Building/BuildingDetails/BuildingSummary/BuildingConstruction/ConditionedFloorArea": "1500",
    "/HPXML/Building/BuildingDetails/Systems/HVAC/HVACPlant/HeatingSystem/HeatingSystemType": "Furnace"
}

response = requests.post(
    "http://localhost:8000/v4.0/validate/document",
    json={
        "document_data": document_data,
        "strict_mode": True,
        "custom_rules": [
            {"type": "numeric_range", "min": 500, "max": 10000}
        ]
    }
)

validation_result = response.json()
if not validation_result["overall_valid"]:
    for result in validation_result["results"]:
        if result["errors"]:
            print(f"Field {result['field_path']}: {result['errors']}")
```

### Schema Exploration

```python
import requests

# Search for specific schema elements
response = requests.get("http://localhost:8000/search",
                       params={"q": "heating", "kind": "field", "limit": 10})
heating_fields = response.json()

# Get metadata about schema
response = requests.get("http://localhost:8000/metadata")
metadata = response.json()
print(f"Schema version: {metadata['version']}")
print(f"Total fields: {metadata['totalFields']}")

# Navigate schema hierarchy
response = requests.get("http://localhost:8000/tree",
                       params={"section": "Building", "depth": 2})
building_tree = response.json()
```

## 📊 Performance & Monitoring

### Performance Metrics

```bash
# Get performance metrics
curl http://localhost:8000/metrics/performance

# Get cache statistics
curl http://localhost:8000/metrics/cache

# Get system metrics
curl http://localhost:8000/metrics/system

# Enhanced health check
curl http://localhost:8000/metrics/health

# Reset metrics (useful for testing)
curl -X POST http://localhost:8000/metrics/reset
```

### Performance Characteristics

| Operation | Response Time | Caching |
|-----------|---------------|---------|
| Schema tree (cached) | <5ms | ✅ TTL-based |
| Field search | <20ms | ✅ Query cache |
| Validation | <10ms | ✅ Rule cache |
| GraphQL queries | <50ms | ✅ Schema cache |

## 🐳 Docker Deployment

### Basic Docker Setup

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install -e .

EXPOSE 8000
CMD ["hpxml-schema-api"]
```

### Docker Compose with Redis

```yaml
version: '3.8'
services:
  hpxml-api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - HPXML_CACHE_TYPE=distributed
      - HPXML_REDIS_URL=redis://redis:6379/0
    depends_on:
      - redis

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

### Environment Configuration

```bash
# Production deployment
docker run -d \
  -p 8000:8000 \
  -e HPXML_CACHE_TYPE=distributed \
  -e HPXML_REDIS_URL=redis://your-redis:6379/0 \
  -e HPXML_CACHE_TTL=7200 \
  -v /path/to/schemas:/schemas \
  -e HPXML_SCHEMA_DIR=/schemas \
  hpxml-schema-api
```

## 🔍 Troubleshooting

### Common Issues

**Schema not found:**
```bash
# Check schema path configuration
curl http://localhost:8000/health
# Look for schema_path in response

# Set schema path explicitly
export HPXML_SCHEMA_PATH=/path/to/HPXML.xsd
```

**Cache issues:**
```bash
# Check cache status
curl http://localhost:8000/metrics/cache

# Clear cache by restarting or using distributed cache reset
curl -X POST http://localhost:8000/metrics/reset
```

**Performance issues:**
```bash
# Check performance metrics
curl http://localhost:8000/metrics/performance

# Reduce query depth
curl "http://localhost:8000/tree?depth=2"  # Instead of default unlimited depth
```

### Logging

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG
hpxml-schema-api

# Check logs for errors
tail -f /var/log/hpxml-schema-api.log
```

## 📚 Additional Resources

- **API Reference**: http://localhost:8000/docs (Interactive Swagger UI)
- **GraphQL Playground**: http://localhost:8000/graphql
- **Python Client Examples**: [examples/](./examples/)
- **HPXML Standard**: [Official HPXML Documentation](https://hpxml.nrel.gov/)

### Development

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Code formatting
black src/ tests/
ruff check src/ tests/

# Type checking
mypy src/
```

## 🤝 Contributing

We welcome contributions! Please:

1. **Fork the repository** and create a feature branch
2. **Add tests** for new functionality (maintain >90% coverage)
3. **Update documentation** as needed
4. **Follow code style** (black + ruff)
5. **Submit a pull request** with clear description

### Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test categories
pytest tests/schema_api/ -v           # Core API tests
pytest tests/test_enhanced_validation.py -v  # Enhanced validation tests
pytest tests/test_version_manager.py -v      # Version management tests

# Check test coverage
pytest --cov=hpxml_schema_api tests/
```

## 📄 License

This project is licensed under the GPL-3.0-or-later License - see the [LICENSE](LICENSE) file for details.

## 📧 Support

- **Issues**: [GitHub Issues](https://github.com/canmet-energy/hpxml-schema-api/issues)
- **Documentation**: [API Documentation](http://localhost:8000/docs)
- **Email**: canmet-energy@nrcan-rncan.gc.ca

---
