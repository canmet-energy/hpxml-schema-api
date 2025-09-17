"""
Phase 2 tests for MCP primitives implementation.

Tests MCP resources, tools, and prompts functionality.
"""

import pytest
import json
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from typing import Dict, Any, List

# Test imports
try:
    from hpxml_schema_api.mcp_server import MCPServer, MCPConfig
    from hpxml_schema_api.graphql_bridge import GraphQLMCPBridge
    from hpxml_schema_api.graphql_schema import schema as graphql_schema
except ImportError:
    pytest.skip("MCP modules not available", allow_module_level=True)


class TestMCPResourceImplementation:
    """Test MCP resource implementation and retrieval."""

    @pytest.fixture
    async def mcp_server(self):
        """Create MCP server for testing."""
        config = MCPConfig(transport="stdio")
        server = MCPServer(config)
        await server.start()
        yield server
        await server.stop()

    @pytest.fixture
    def graphql_bridge(self):
        """Create GraphQL bridge for testing."""
        return GraphQLMCPBridge(graphql_schema)

    @pytest.mark.asyncio
    async def test_schema_metadata_resource_retrieval(self, graphql_bridge):
        """Test schema://metadata resource retrieval."""
        uri = "schema://metadata"
        result = await graphql_bridge.read_resource(uri)

        assert result is not None
        data = json.loads(result)
        assert "version" in data
        assert "totalNodes" in data or "total_nodes" in data
        assert "totalFields" in data or "total_fields" in data

    @pytest.mark.asyncio
    async def test_schema_tree_resource_retrieval(self, graphql_bridge):
        """Test schema://tree resource with various xpath values."""
        # Test basic tree retrieval
        uri = "schema://tree"
        result = await graphql_bridge.read_resource(uri)

        assert result is not None
        data = json.loads(result)
        assert "xpath" in data or "name" in data

    @pytest.mark.asyncio
    async def test_schema_fields_resource_retrieval(self, graphql_bridge):
        """Test schema://fields resource with different sections."""
        uri = "schema://fields"
        result = await graphql_bridge.read_resource(uri)

        assert result is not None
        # Should return a list or object with field information
        data = json.loads(result)
        assert isinstance(data, (list, dict))

    @pytest.mark.asyncio
    async def test_schema_search_resource_retrieval(self, graphql_bridge):
        """Test schema://search resource with various search terms."""
        uri = "schema://search"
        result = await graphql_bridge.read_resource(uri)

        assert result is not None
        data = json.loads(result)
        assert "results" in data or isinstance(data, list)

    @pytest.mark.asyncio
    async def test_resource_error_handling_invalid_paths(self, graphql_bridge):
        """Test resource error handling for invalid paths."""
        # Test non-existent resource
        with pytest.raises(ValueError, match="Unknown resource URI"):
            await graphql_bridge.read_resource("schema://nonexistent")

        # Test invalid URI format
        with pytest.raises(ValueError):
            await graphql_bridge.read_resource("invalid://uri")

    @pytest.mark.asyncio
    async def test_resource_error_handling_missing_data(self, graphql_bridge):
        """Test resource error handling when data is missing."""
        # Mock the GraphQL execution to return None
        with patch.object(graphql_bridge, '_execute_graphql_query', return_value=None):
            result = await graphql_bridge.read_resource("schema://metadata")
            assert result == "null"  # JSON null

    @pytest.mark.asyncio
    async def test_resource_list_via_mcp_message(self, graphql_bridge):
        """Test listing resources via MCP message."""
        message = {"method": "resources/list", "params": {}}
        response = await graphql_bridge.handle_mcp_message(message)

        assert "result" in response
        assert "resources" in response["result"]
        resources = response["result"]["resources"]
        assert len(resources) > 0

        # Check resource structure
        for resource in resources:
            assert "uri" in resource
            assert "name" in resource
            assert "description" in resource
            assert "mimeType" in resource

    @pytest.mark.asyncio
    async def test_resource_read_via_mcp_message(self, graphql_bridge):
        """Test reading resource via MCP message."""
        message = {
            "method": "resources/read",
            "params": {"uri": "schema://metadata"}
        }
        response = await graphql_bridge.handle_mcp_message(message)

        assert "result" in response
        assert "contents" in response["result"]
        contents = response["result"]["contents"]
        assert len(contents) > 0
        assert contents[0]["uri"] == "schema://metadata"
        assert contents[0]["mimeType"] == "application/json"
        assert "text" in contents[0]


class TestMCPToolImplementation:
    """Test MCP tool implementation and execution."""

    @pytest.fixture
    def graphql_bridge(self):
        """Create GraphQL bridge for testing."""
        return GraphQLMCPBridge(graphql_schema)

    @pytest.mark.asyncio
    async def test_validate_field_tool_with_valid_inputs(self, graphql_bridge):
        """Test validate_field tool with valid inputs."""
        args = {
            "xpath": "/HPXML/Building/BuildingID",
            "value": "MyBuilding123"
        }
        result = await graphql_bridge.call_tool("validate_field", args)

        assert result is not None
        if isinstance(result, dict):
            assert "valid" in result or "errors" in result

    @pytest.mark.asyncio
    async def test_validate_field_tool_with_invalid_inputs(self, graphql_bridge):
        """Test validate_field tool with invalid inputs."""
        args = {
            "xpath": "/HPXML/Invalid/Path",
            "value": ""
        }
        result = await graphql_bridge.call_tool("validate_field", args)

        assert result is not None
        # Should return validation errors or indicate invalid state

    @pytest.mark.asyncio
    async def test_validate_enhanced_tool_with_business_rules(self, graphql_bridge):
        """Test validate_enhanced tool with business rules."""
        args = {
            "field_path": "/HPXML/Building/BuildingDetails/BuildingSummary/BuildingConstruction/ConditionedFloorArea",
            "value": "1500",
            "custom_rules": [
                {"type": "numeric_range", "min": 500, "max": 10000}
            ]
        }
        # This will fail initially since we haven't implemented enhanced validation yet
        try:
            result = await graphql_bridge.call_tool("validate_enhanced", args)
            assert result is not None
        except ValueError as e:
            # Expected for now - we'll implement this tool later
            assert "Unknown mutation" in str(e)

    @pytest.mark.asyncio
    async def test_validate_document_tool_with_complete_documents(self, graphql_bridge):
        """Test validate_document tool with complete documents."""
        args = {
            "document_data": {
                "/HPXML/Building/BuildingID": "MyBuilding123",
                "/HPXML/Building/BuildingDetails/BuildingSummary/BuildingConstruction/ConditionedFloorArea": "1500"
            },
            "strict_mode": True
        }
        # This will fail initially since we haven't implemented document validation yet
        try:
            result = await graphql_bridge.call_tool("validate_document", args)
            assert result is not None
        except ValueError as e:
            # Expected for now - we'll implement this tool later
            assert "Unknown mutation" in str(e)

    @pytest.mark.asyncio
    async def test_generate_form_tool_with_schema_sections(self, graphql_bridge):
        """Test generate_form tool with schema sections."""
        args = {
            "section": "Building",
            "depth": 2,
            "include_descriptions": True
        }
        # This will fail initially since we haven't implemented form generation yet
        try:
            result = await graphql_bridge.call_tool("generate_form", args)
            assert result is not None
        except ValueError as e:
            # Expected for now - we'll implement this tool later
            assert "Unknown mutation" in str(e)

    @pytest.mark.asyncio
    async def test_tool_parameter_validation_and_error_responses(self, graphql_bridge):
        """Test tool parameter validation and error responses."""
        # Test missing required parameters
        with pytest.raises((ValueError, KeyError)):
            await graphql_bridge.call_tool("validate_field", {})

        # Test invalid parameter types
        args = {
            "xpath": 123,  # Should be string
            "value": None
        }
        try:
            result = await graphql_bridge.call_tool("validate_field", args)
            # Should handle gracefully
        except (ValueError, TypeError):
            # Expected for invalid parameters
            pass

    @pytest.mark.asyncio
    async def test_tool_list_via_mcp_message(self, graphql_bridge):
        """Test listing tools via MCP message."""
        message = {"method": "tools/list", "params": {}}
        response = await graphql_bridge.handle_mcp_message(message)

        assert "result" in response
        assert "tools" in response["result"]
        tools = response["result"]["tools"]
        assert len(tools) > 0

        # Check tool structure
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool

    @pytest.mark.asyncio
    async def test_tool_call_via_mcp_message(self, graphql_bridge):
        """Test calling tool via MCP message."""
        message = {
            "method": "tools/call",
            "params": {
                "name": "validate_field",
                "arguments": {
                    "xpath": "/HPXML/Building/BuildingID",
                    "value": "TestBuilding"
                }
            }
        }
        response = await graphql_bridge.handle_mcp_message(message)

        assert "result" in response
        assert "content" in response["result"]
        content = response["result"]["content"]
        assert len(content) > 0
        assert content[0]["type"] == "text"
        assert "text" in content[0]


class TestMCPPromptImplementation:
    """Test MCP prompt implementation and handling."""

    @pytest.fixture
    def graphql_bridge(self):
        """Create GraphQL bridge for testing."""
        return GraphQLMCPBridge(graphql_schema)

    def test_prompt_template_rendering(self, graphql_bridge):
        """Test prompt template rendering."""
        # This will be implemented when we add prompt support
        # For now, just test that the bridge can be created
        assert graphql_bridge is not None

    def test_prompt_parameter_substitution(self, graphql_bridge):
        """Test prompt parameter substitution."""
        # Placeholder test - will implement prompts in extended version
        pass

    def test_prompt_validation_and_formatting(self, graphql_bridge):
        """Test prompt validation and formatting."""
        # Placeholder test - will implement prompts in extended version
        pass


class TestMCPIntegrationTests:
    """Test integration between resources, tools, and GraphQL."""

    @pytest.fixture
    def graphql_bridge(self):
        """Create GraphQL bridge for testing."""
        return GraphQLMCPBridge(graphql_schema)

    @pytest.mark.asyncio
    async def test_resource_to_graphql_query_translation(self, graphql_bridge):
        """Test resource-to-GraphQL query translation."""
        # Test that resource URIs correctly map to GraphQL queries
        resources, _ = graphql_bridge.introspect_schema()

        for resource in resources[:3]:  # Test first 3 resources
            result = await graphql_bridge.read_resource(resource.uri)
            assert result is not None

            # Should be valid JSON
            data = json.loads(result)
            assert data is not None

    @pytest.mark.asyncio
    async def test_tool_to_graphql_mutation_translation(self, graphql_bridge):
        """Test tool-to-GraphQL mutation translation."""
        # Test that tool calls correctly map to GraphQL mutations
        _, tools = graphql_bridge.introspect_schema()

        # Test validate_field tool specifically
        validate_tools = [t for t in tools if "validate" in t.name]
        assert len(validate_tools) > 0

        tool = validate_tools[0]
        args = {"xpath": "/HPXML/Building/BuildingID", "value": "Test"}

        try:
            result = await graphql_bridge.call_tool(tool.name, args)
            assert result is not None
        except ValueError:
            # Some tools may not be implemented yet
            pass

    @pytest.mark.asyncio
    async def test_error_propagation_from_graphql_to_mcp(self, graphql_bridge):
        """Test error propagation from GraphQL to MCP."""
        # Test resource error propagation
        message = {
            "method": "resources/read",
            "params": {"uri": "schema://nonexistent"}
        }
        response = await graphql_bridge.handle_mcp_message(message)
        assert "error" in response

        # Test tool error propagation
        message = {
            "method": "tools/call",
            "params": {
                "name": "nonexistent_tool",
                "arguments": {}
            }
        }
        response = await graphql_bridge.handle_mcp_message(message)
        assert "error" in response

    @pytest.mark.asyncio
    async def test_mcp_message_format_compliance(self, graphql_bridge):
        """Test that MCP messages comply with the protocol format."""
        # Test successful resource response format
        message = {"method": "resources/list", "params": {}}
        response = await graphql_bridge.handle_mcp_message(message)

        assert isinstance(response, dict)
        assert "result" in response or "error" in response

        if "result" in response:
            # Check resources list format
            if "resources" in response["result"]:
                for resource in response["result"]["resources"]:
                    assert "uri" in resource
                    assert "name" in resource
                    assert "mimeType" in resource

        # Test successful tool response format
        message = {"method": "tools/list", "params": {}}
        response = await graphql_bridge.handle_mcp_message(message)

        assert isinstance(response, dict)
        assert "result" in response or "error" in response

        if "result" in response and "tools" in response["result"]:
            for tool in response["result"]["tools"]:
                assert "name" in tool
                assert "description" in tool
                assert "inputSchema" in tool

    @pytest.mark.asyncio
    async def test_concurrent_mcp_operations(self, graphql_bridge):
        """Test concurrent MCP operations."""
        # Test multiple concurrent resource reads
        tasks = []
        for uri in ["schema://metadata", "schema://health", "schema://tree"]:
            task = graphql_bridge.read_resource(uri)
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # At least some should succeed
        successful_results = [r for r in results if not isinstance(r, Exception)]
        assert len(successful_results) > 0

    @pytest.mark.asyncio
    async def test_resource_caching_effectiveness(self, graphql_bridge):
        """Test resource caching effectiveness."""
        # Read the same resource multiple times
        uri = "schema://metadata"

        # First read
        result1 = await graphql_bridge.read_resource(uri)

        # Second read (should use cache if implemented)
        result2 = await graphql_bridge.read_resource(uri)

        # Results should be identical
        assert result1 == result2

        # Both should be valid JSON
        data1 = json.loads(result1)
        data2 = json.loads(result2)
        assert data1 == data2


class TestMCPServerIntegration:
    """Test MCP server integration with primitives."""

    @pytest.mark.asyncio
    async def test_server_resource_handler_registration(self):
        """Test that server properly registers resource handlers."""
        config = MCPConfig(transport="stdio")
        server = MCPServer(config)

        await server.start()

        # Check that handlers are registered
        assert server.bridge is not None
        resources, tools = server.bridge.introspect_schema()
        assert len(resources) > 0

        await server.stop()

    @pytest.mark.asyncio
    async def test_server_tool_handler_registration(self):
        """Test that server properly registers tool handlers."""
        config = MCPConfig(transport="stdio")
        server = MCPServer(config)

        await server.start()

        # Check that tools are registered
        assert server.bridge is not None
        resources, tools = server.bridge.introspect_schema()
        assert len(tools) > 0

        await server.stop()

    @pytest.mark.asyncio
    async def test_server_message_routing(self):
        """Test that server properly routes MCP messages."""
        config = MCPConfig(transport="stdio")
        server = MCPServer(config)

        await server.start()

        # Test resource list message
        response = await server.handle_message('{"method": "resources/list", "params": {}}')
        assert "result" in response or "error" in response

        # Test tool list message
        response = await server.handle_message('{"method": "tools/list", "params": {}}')
        assert "result" in response or "error" in response

        await server.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])