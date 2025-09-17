"""
Phase 1 tests for MCP server implementation.

Tests MCP server initialization, GraphQL bridge setup, and transport layer.
"""

import pytest
import os
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from pathlib import Path

# Test imports - these will fail initially until we implement the modules
try:
    from hpxml_schema_api.mcp_server import MCPServer, MCPConfig
    from hpxml_schema_api.graphql_bridge import GraphQLMCPBridge
except ImportError:
    # Expected during TDD - we'll implement these modules next
    MCPServer = None
    MCPConfig = None
    GraphQLMCPBridge = None

    # Create mock classes for testing structure
    class MCPServer:
        def __init__(self, config):
            self.config = config
            self.running = False

        async def start(self):
            self.running = True

        async def stop(self):
            self.running = False

    class MCPConfig:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class GraphQLMCPBridge:
        def __init__(self, schema):
            self.schema = schema


class TestMCPServerInitialization:
    """Test MCP server initialization and configuration."""

    def test_mcp_config_creation(self):
        """Test MCP configuration object creation."""
        config = MCPConfig(
            transport="stdio",
            auth_token=None,
            graphql_endpoint="http://localhost:8000/graphql"
        )

        assert config.transport == "stdio"
        assert config.auth_token is None
        assert config.graphql_endpoint == "http://localhost:8000/graphql"

    def test_mcp_config_with_environment_variables(self):
        """Test configuration loading from environment variables."""
        with patch.dict(os.environ, {
            'MCP_TRANSPORT': 'http',
            'MCP_AUTH_TOKEN': 'test-token-123',
            'MCP_GRAPHQL_ENDPOINT': 'http://localhost:8080/graphql'
        }):
            config = MCPConfig.from_env()

            assert config.transport == "http"
            assert config.auth_token == "test-token-123"
            assert config.graphql_endpoint == "http://localhost:8080/graphql"

    def test_mcp_config_defaults(self):
        """Test configuration defaults when no environment variables set."""
        with patch.dict(os.environ, {}, clear=True):
            config = MCPConfig.from_env()

            assert config.transport == "stdio"  # Default transport
            assert config.auth_token is None
            assert "graphql" in config.graphql_endpoint

    def test_mcp_server_creation_with_stdio(self):
        """Test MCP server creation with stdio transport."""
        config = MCPConfig(transport="stdio")
        server = MCPServer(config)

        assert server.config.transport == "stdio"
        assert not server.running

    def test_mcp_server_creation_with_http(self):
        """Test MCP server creation with HTTP transport."""
        config = MCPConfig(transport="http", port=8001)
        server = MCPServer(config)

        assert server.config.transport == "http"
        assert server.config.port == 8001
        assert not server.running

    def test_mcp_server_creation_with_invalid_transport(self):
        """Test MCP server creation with invalid transport raises error."""
        config = MCPConfig(transport="invalid")

        with pytest.raises(ValueError, match="Unsupported transport"):
            MCPServer(config)

    @pytest.mark.asyncio
    async def test_mcp_server_startup_shutdown(self):
        """Test MCP server startup and shutdown."""
        config = MCPConfig(transport="stdio")
        server = MCPServer(config)

        # Test startup
        await server.start()
        assert server.running

        # Test shutdown
        await server.stop()
        assert not server.running


class TestGraphQLBridgeSetup:
    """Test GraphQL-to-MCP bridge initialization and setup."""

    def test_graphql_bridge_creation(self):
        """Test GraphQL bridge creation with schema."""
        from hpxml_schema_api.graphql_schema import schema

        bridge = GraphQLMCPBridge(schema)
        assert bridge.schema is not None

    def test_graphql_bridge_introspection(self):
        """Test GraphQL schema introspection for MCP mapping."""
        from hpxml_schema_api.graphql_schema import schema

        bridge = GraphQLMCPBridge(schema)
        resources, tools = bridge.introspect_schema()

        # Should find query operations as resources
        assert len(resources) > 0
        assert any("metadata" in r.name for r in resources)
        assert any("tree" in r.name for r in resources)
        assert any("search" in r.name for r in resources)

        # Should find mutation operations as tools
        assert len(tools) > 0
        assert any("validate" in t.name for t in tools)

    def test_graphql_query_to_mcp_resource_mapping(self):
        """Test mapping GraphQL queries to MCP resources."""
        from hpxml_schema_api.graphql_schema import schema

        bridge = GraphQLMCPBridge(schema)

        # Test metadata query mapping
        resource = bridge.map_query_to_resource("metadata")
        assert resource is not None
        assert resource.name == "metadata"
        assert str(resource.uri).startswith("schema://")

        # Test tree query mapping
        resource = bridge.map_query_to_resource("tree")
        assert resource is not None
        assert resource.name == "tree"
        assert "tree" in resource.description.lower() or "structure" in resource.description.lower()

    def test_graphql_mutation_to_mcp_tool_mapping(self):
        """Test mapping GraphQL mutations to MCP tools."""
        from hpxml_schema_api.graphql_schema import schema

        bridge = GraphQLMCPBridge(schema)

        # Test validate_field mutation mapping
        tool = bridge.map_mutation_to_tool("validate_field")
        assert tool is not None
        assert tool.name == "validate_field"
        assert "field" in tool.description.lower() or "validation" in tool.description.lower()

    def test_graphql_bridge_schema_validation(self):
        """Test GraphQL bridge validates schema structure."""
        # Test with None schema
        with pytest.raises(ValueError, match="Invalid GraphQL schema"):
            GraphQLMCPBridge(None)

        # Test with object that has no query or _schema attribute
        invalid_schema = Mock()
        # Remove any query or _schema attributes
        if hasattr(invalid_schema, 'query'):
            delattr(invalid_schema, 'query')
        if hasattr(invalid_schema, '_schema'):
            delattr(invalid_schema, '_schema')

        with pytest.raises(ValueError, match="Invalid GraphQL schema"):
            GraphQLMCPBridge(invalid_schema)

    def test_graphql_bridge_error_handling(self):
        """Test GraphQL bridge handles introspection errors gracefully."""
        from hpxml_schema_api.graphql_schema import schema

        bridge = GraphQLMCPBridge(schema)

        # Test with non-existent query
        resource = bridge.map_query_to_resource("nonexistent")
        assert resource is None

        # Test with non-existent mutation
        tool = bridge.map_mutation_to_tool("nonexistent")
        assert tool is None


class TestTransportLayer:
    """Test MCP transport layer functionality."""

    @pytest.mark.asyncio
    async def test_stdio_transport_message_handling(self):
        """Test stdio transport handles messages correctly."""
        config = MCPConfig(transport="stdio")
        server = MCPServer(config)

        # Mock stdin/stdout for testing
        with patch('sys.stdin') as mock_stdin, patch('sys.stdout') as mock_stdout:
            mock_stdin.readline.return_value = '{"method": "ping"}\n'

            await server.start()

            # Simulate message processing
            response = await server.handle_message('{"method": "ping"}')
            assert response is not None
            assert "result" in response or "error" in response

    @pytest.mark.asyncio
    async def test_http_transport_endpoint_creation(self):
        """Test HTTP transport creates endpoints correctly."""
        config = MCPConfig(transport="http", port=8001)
        server = MCPServer(config)

        await server.start()

        # Check that HTTP server is configured
        assert hasattr(server, 'http_server')
        assert server.http_server is not None

        await server.stop()

    def test_transport_selection_via_environment(self):
        """Test transport selection via environment variables."""
        # Test stdio selection
        with patch.dict(os.environ, {'MCP_TRANSPORT': 'stdio'}):
            config = MCPConfig.from_env()
            server = MCPServer(config)
            assert server.config.transport == "stdio"

        # Test HTTP selection
        with patch.dict(os.environ, {'MCP_TRANSPORT': 'http'}):
            config = MCPConfig.from_env()
            server = MCPServer(config)
            assert server.config.transport == "http"

    @pytest.mark.asyncio
    async def test_authentication_middleware_setup(self):
        """Test authentication middleware is properly configured."""
        config = MCPConfig(
            transport="http",
            auth_token="test-token-123",
            require_auth=True
        )
        server = MCPServer(config)

        await server.start()

        # Check authentication middleware is configured
        assert hasattr(server, 'auth_middleware')
        assert server.auth_middleware is not None

        await server.stop()

    @pytest.mark.asyncio
    async def test_unauthenticated_request_rejection(self):
        """Test that unauthenticated requests are rejected when auth is required."""
        config = MCPConfig(
            transport="http",
            auth_token="test-token-123",
            require_auth=True
        )
        server = MCPServer(config)

        await server.start()

        # Test request without token
        response = await server.handle_message('{"method": "ping"}', auth_token=None)
        assert "error" in response
        assert "authentication" in response["error"]["message"].lower()

        await server.stop()

    @pytest.mark.asyncio
    async def test_authenticated_request_acceptance(self):
        """Test that authenticated requests are accepted."""
        config = MCPConfig(
            transport="http",
            auth_token="test-token-123",
            require_auth=True
        )
        server = MCPServer(config)

        await server.start()

        # Test request with correct token
        response = await server.handle_message(
            '{"method": "ping"}',
            auth_token="test-token-123"
        )
        assert "result" in response or ("error" in response and "authentication" not in response["error"]["message"].lower())

        await server.stop()


class TestMCPServerDependencies:
    """Test MCP server handles missing dependencies gracefully."""

    def test_mcp_server_with_missing_dependencies(self):
        """Test MCP server initialization when dependencies are missing."""
        # Mock the MCP_AVAILABLE flag and test the error handling
        with patch('hpxml_schema_api.mcp_server.MCP_AVAILABLE', False):
            with patch('hpxml_schema_api.mcp_server.MCP_IMPORT_ERROR', "mcp not found"):
                from hpxml_schema_api.mcp_server import MCPServer, MCPConfig
                config = MCPConfig(transport="stdio")
                with pytest.raises(ImportError, match="MCP dependencies not installed"):
                    MCPServer(config)

    def test_graceful_fallback_when_mcp_unavailable(self):
        """Test that the main app works when MCP is not available."""
        # This test ensures the main API still works without MCP
        from hpxml_schema_api.app import app

        # App should still be importable and functional
        assert app is not None
        assert hasattr(app, 'routes')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])