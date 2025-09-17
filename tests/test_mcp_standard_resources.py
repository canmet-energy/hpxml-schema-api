"""Integration test for standard MCP resources (metadata, tree).

Ensures the generic dispatcher path used for the schema versions resource also
works for regular resources exposed by the GraphQL bridge.
"""

import json

import pytest

from hpxml_schema_api.mcp_server import MCPConfig, MCPServer


@pytest.mark.asyncio
async def test_mcp_metadata_and_tree_resources():
    server = MCPServer(MCPConfig(transport="stdio"))
    await server.start()

    # List resources and confirm metadata/tree are present
    list_response = await server.handle_message(
        json.dumps({"method": "list_resources"})
    )
    assert "result" in list_response
    uris = {r["uri"] for r in list_response["result"]}
    # Resources are namespaced (e.g., schema://metadata)
    assert any(u.endswith("metadata") for u in uris)
    assert any(u.endswith("tree") for u in uris)

    # Read metadata resource
    metadata_uri = next(u for u in uris if u.endswith("metadata"))
    read_metadata = await server.handle_message(
        json.dumps({"method": "read_resource", "params": {"uri": metadata_uri}})
    )
    assert "result" in read_metadata
    # Metadata payload should be JSON string (bridge returns serialized JSON)
    assert "version" in read_metadata["result"] or "version" in str(
        read_metadata["result"]
    )  # lenient

    # Read tree resource with default depth
    tree_uri = next(u for u in uris if u.endswith("tree"))
    read_tree = await server.handle_message(
        json.dumps({"method": "read_resource", "params": {"uri": tree_uri}})
    )
    assert "result" in read_tree
    # Expect tree JSON string containing root element name HPXML (case-insensitive check)
    assert "hpxml" in read_tree["result"].lower()

    await server.stop()
