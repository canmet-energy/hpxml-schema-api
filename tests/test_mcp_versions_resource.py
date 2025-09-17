"""Test for MCP schema_versions resource.

This test exercises the server's ability to surface the schema_versions://local
resource added to the MCP resource inventory. It invokes the underlying
_resource listing then read handler indirectly via the public handle_message API.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from hpxml_schema_api.mcp_server import MCPConfig, MCPServer
from hpxml_schema_api.version_manager import SchemaVersionInfo


@pytest.mark.asyncio
async def test_mcp_schema_versions_resource():
    # Mock MCP dependency availability implicitly (imports succeed if extras installed)
    # Patch version manager to supply deterministic versions
    with patch("hpxml_schema_api.mcp_server._build_versions_payload") as mock_payload:
        mock_payload.return_value = {
            "default_version": "4.0",
            "versions": [
                {"version": "4.1", "endpoints": {"metadata": "/v4.1/metadata"}},
                {"version": "4.0", "endpoints": {"metadata": "/v4.0/metadata"}},
            ],
        }

        config = MCPConfig(transport="stdio")
        server = MCPServer(config)

        # Start (register handlers); avoid infinite loop by not calling run_server
        await server.start()

        # List resources (simulate an MCP list_resources request)
        list_request = json.dumps({"method": "list_resources"})
        list_response = await server.handle_message(list_request)
        serialized = json.dumps(list_response)
        assert "mcp://schema_versions" in serialized

        # Read the resource
        read_request = json.dumps(
            {"method": "read_resource", "params": {"uri": "mcp://schema_versions"}}
        )
        read_response = await server.handle_message(read_request)
        response_text = json.dumps(read_response)
        assert "4.1" in response_text and "4.0" in response_text

        await server.stop()
