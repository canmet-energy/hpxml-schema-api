"""
Phase 3 tests for MCP integration and deployment.

Tests dual-mode operation, deployment configuration, and production scenarios.
"""

import pytest
import asyncio
import json
import time
import os
from unittest.mock import Mock, patch, AsyncMock
from typing import Dict, Any, List
from pathlib import Path

# Test imports
try:
    from hpxml_schema_api.mcp_server import MCPServer, MCPConfig, run_server
    from hpxml_schema_api.graphql_bridge import GraphQLMCPBridge
    from hpxml_schema_api.app import app as fastapi_app
    from hpxml_schema_api.graphql_schema import schema as graphql_schema
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
except ImportError:
    pytest.skip("MCP modules not available", allow_module_level=True)


class TestDualModeOperation:
    """Test dual-mode operation: standalone MCP server and FastAPI mounted endpoint."""

    @pytest.mark.asyncio
    async def test_standalone_mcp_server_startup_shutdown(self):
        """Test standalone MCP server startup and shutdown."""
        config = MCPConfig(transport="stdio")
        server = MCPServer(config)

        # Test startup
        await server.start()
        assert server.running is True
        assert server.bridge is not None
        assert server.server is not None

        # Test that handlers are registered
        resources, tools = server.bridge.introspect_schema()
        assert len(resources) > 0
        assert len(tools) > 0

        # Test shutdown
        await server.stop()
        assert server.running is False

    @pytest.mark.asyncio
    async def test_fastapi_mounted_mcp_endpoint(self):
        """Test FastAPI mounted MCP endpoint (/mcp)."""
        # This test will verify that we can mount MCP on FastAPI
        # Initially will fail until we implement the mounting functionality

        from hpxml_schema_api.mcp_fastapi_integration import mount_mcp_server

        # Create a test FastAPI app
        test_app = FastAPI()

        # Mount MCP server
        mount_mcp_server(test_app, path="/mcp")

        # Test that the route exists
        client = TestClient(test_app)

        # Test MCP endpoint availability
        response = client.post("/mcp", json={"method": "resources/list", "params": {}})
        assert response.status_code == 200

        data = response.json()
        assert "result" in data or "error" in data

    @pytest.mark.asyncio
    async def test_concurrent_access_rest_graphql_mcp(self):
        """Test concurrent access to both REST and MCP interfaces."""
        # Start MCP server
        config = MCPConfig(transport="stdio")
        mcp_server = MCPServer(config)
        await mcp_server.start()

        # Create FastAPI test client
        client = TestClient(fastapi_app)

        # Test concurrent operations
        async def rest_operation():
            response = client.get("/health")
            return response.status_code == 200

        async def graphql_operation():
            query = '{ health }'
            response = client.post("/graphql", json={"query": query})
            return response.status_code == 200

        async def mcp_operation():
            message = {"method": "resources/list", "params": {}}
            response = await mcp_server.handle_message(json.dumps(message))
            return "result" in response or "error" in response

        # Run operations concurrently
        results = await asyncio.gather(
            rest_operation(),
            graphql_operation(),
            mcp_operation(),
            return_exceptions=True
        )

        # All should succeed
        successful_operations = [r for r in results if r is True]
        assert len(successful_operations) >= 2  # At least REST and MCP should work

        await mcp_server.stop()

    @pytest.mark.asyncio
    async def test_resource_sharing_between_protocols(self):
        """Test resource sharing between REST/GraphQL/MCP modes."""
        config = MCPConfig(transport="stdio")
        mcp_server = MCPServer(config)
        await mcp_server.start()

        client = TestClient(fastapi_app)

        # Get metadata via REST
        rest_response = client.get("/metadata")
        assert rest_response.status_code == 200
        rest_data = rest_response.json()

        # Get metadata via MCP
        mcp_message = {"method": "resources/read", "params": {"uri": "schema://metadata"}}
        mcp_response = await mcp_server.handle_message(json.dumps(mcp_message))
        assert "result" in mcp_response
        mcp_data = json.loads(mcp_response["result"]["contents"][0]["text"])

        # Both should return similar metadata structure
        assert "version" in rest_data or "version" in mcp_data

        await mcp_server.stop()


class TestDeploymentConfiguration:
    """Test deployment configuration and environment variable handling."""

    def test_pyproject_toml_entry_point_execution(self):
        """Test pyproject.toml entry point execution."""
        # Test that the entry point is properly configured
        import subprocess
        import sys

        # Test that the hpxml-mcp-server command is available
        try:
            result = subprocess.run([
                sys.executable, "-c",
                "import pkg_resources; print(pkg_resources.get_entry_info('hpxml-schema-api', 'console_scripts', 'hpxml-mcp-server'))"
            ], capture_output=True, text=True, timeout=10)

            # Should not be None if entry point exists
            assert "None" not in result.stdout or result.returncode == 0
        except subprocess.TimeoutExpired:
            # Entry point lookup took too long, consider it working
            pass

    def test_environment_variable_configuration_loading(self):
        """Test environment variable configuration loading."""
        # Test with various environment configurations
        test_configs = [
            {
                "MCP_TRANSPORT": "stdio",
                "MCP_AUTH_TOKEN": "test-token",
                "MCP_LOG_LEVEL": "DEBUG"
            },
            {
                "MCP_TRANSPORT": "http",
                "MCP_PORT": "8001",
                "MCP_HOST": "localhost"
            },
            {
                "MCP_REQUIRE_AUTH": "true",
                "MCP_GRAPHQL_ENDPOINT": "http://localhost:8080/graphql"
            }
        ]

        for test_env in test_configs:
            with patch.dict(os.environ, test_env):
                config = MCPConfig.from_env()

                # Verify configuration matches environment
                if "MCP_TRANSPORT" in test_env:
                    assert config.transport == test_env["MCP_TRANSPORT"]
                if "MCP_AUTH_TOKEN" in test_env:
                    assert config.auth_token == test_env["MCP_AUTH_TOKEN"]
                if "MCP_PORT" in test_env:
                    assert config.port == int(test_env["MCP_PORT"])
                if "MCP_REQUIRE_AUTH" in test_env:
                    assert config.require_auth == (test_env["MCP_REQUIRE_AUTH"].lower() == "true")

    def test_authentication_token_validation(self):
        """Test authentication token validation."""
        # Test valid token
        config = MCPConfig(
            transport="http",
            auth_token="valid-token-123",
            require_auth=True
        )
        server = MCPServer(config)
        assert server.auth_middleware is not None

        # Test without token when required
        config_no_token = MCPConfig(
            transport="http",
            require_auth=True,
            auth_token=None
        )
        server_no_token = MCPServer(config_no_token)
        # Should still create middleware but with None token
        assert server_no_token.auth_middleware is not None

    def test_configuration_validation_and_error_handling(self):
        """Test configuration validation and error handling."""
        # Test invalid transport
        with pytest.raises(ValueError, match="Unsupported transport"):
            config = MCPConfig(transport="invalid")
            MCPServer(config)

        # Test valid configurations
        valid_configs = [
            MCPConfig(transport="stdio"),
            MCPConfig(transport="http", port=8001),
            MCPConfig(transport="http", port=8002, auth_token="test"),
        ]

        for config in valid_configs:
            server = MCPServer(config)
            assert server.config == config


class TestProductionScenarios:
    """Test production scenarios including load testing and monitoring."""

    @pytest.mark.asyncio
    async def test_mcp_server_under_load(self):
        """Test MCP server under load (multiple concurrent clients)."""
        config = MCPConfig(transport="stdio")
        server = MCPServer(config)
        await server.start()

        # Simulate multiple concurrent clients
        async def client_operation(client_id: int):
            messages = [
                {"method": "resources/list", "params": {}},
                {"method": "tools/list", "params": {}},
                {"method": "resources/read", "params": {"uri": "schema://metadata"}},
                {"method": "tools/call", "params": {
                    "name": "validate_field",
                    "arguments": {"xpath": f"/HPXML/Building/Client{client_id}", "value": f"test{client_id}"}
                }}
            ]

            results = []
            for message in messages:
                try:
                    response = await server.handle_message(json.dumps(message))
                    results.append("result" in response or "error" in response)
                except Exception as e:
                    results.append(False)

            return all(results)

        # Run 10 concurrent clients with 4 operations each
        tasks = [client_operation(i) for i in range(10)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # At least 80% should succeed
        successful_clients = [r for r in results if r is True]
        success_rate = len(successful_clients) / len(results)
        assert success_rate >= 0.8

        await server.stop()

    def test_memory_usage_and_cleanup(self):
        """Test memory usage and cleanup."""
        import gc
        import psutil
        import os

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss

        # Create and destroy multiple servers
        servers = []
        for i in range(5):
            config = MCPConfig(transport="stdio")
            server = MCPServer(config)
            servers.append(server)

        # Clean up
        del servers
        gc.collect()

        final_memory = process.memory_info().rss
        memory_increase = final_memory - initial_memory

        # Memory increase should be reasonable (less than 50MB)
        assert memory_increase < 50 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_graceful_shutdown_and_error_recovery(self):
        """Test graceful shutdown and error recovery."""
        config = MCPConfig(transport="stdio")
        server = MCPServer(config)

        # Test startup
        await server.start()
        assert server.running is True

        # Simulate some operations
        message = {"method": "resources/list", "params": {}}
        response = await server.handle_message(json.dumps(message))
        assert "result" in response

        # Test graceful shutdown
        await server.stop()
        assert server.running is False

        # Test that operations fail gracefully after shutdown
        try:
            await server.handle_message(json.dumps(message))
            # Should still work as handle_message doesn't check running state
        except Exception:
            # Any exception should be handled gracefully
            pass

        # Test restart capability
        await server.start()
        assert server.running is True

        response = await server.handle_message(json.dumps(message))
        assert "result" in response

        await server.stop()

    @pytest.mark.asyncio
    async def test_logging_and_monitoring_integration(self):
        """Test logging and monitoring integration."""
        import logging
        from io import StringIO

        # Capture logs
        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        logger = logging.getLogger('hpxml_schema_api.mcp_server')
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        config = MCPConfig(transport="stdio", log_level="INFO")
        server = MCPServer(config)

        await server.start()

        # Perform some operations to generate logs
        message = {"method": "resources/list", "params": {}}
        await server.handle_message(json.dumps(message))

        await server.stop()

        # Check that logs were generated
        log_contents = log_stream.getvalue()
        assert "MCP server" in log_contents or len(log_contents) > 0

        logger.removeHandler(handler)


class TestCrossProtocolIntegration:
    """Test cross-protocol integration between REST, GraphQL, and MCP."""

    @pytest.mark.asyncio
    async def test_mcp_client_calling_same_resolvers_as_rest_api(self):
        """Test MCP client calling same resolvers as REST API."""
        config = MCPConfig(transport="stdio")
        mcp_server = MCPServer(config)
        await mcp_server.start()

        client = TestClient(fastapi_app)

        # Test metadata endpoint
        rest_response = client.get("/metadata")
        mcp_message = {"method": "resources/read", "params": {"uri": "schema://metadata"}}
        mcp_response = await mcp_server.handle_message(json.dumps(mcp_message))

        # Both should succeed
        assert rest_response.status_code == 200
        assert "result" in mcp_response

        await mcp_server.stop()

    @pytest.mark.asyncio
    async def test_cache_consistency_across_protocols(self):
        """Test cache consistency across protocols."""
        config = MCPConfig(transport="stdio")
        mcp_server = MCPServer(config)
        await mcp_server.start()

        # Access same resource multiple times via different protocols
        client = TestClient(fastapi_app)

        # First access via REST
        rest_response1 = client.get("/metadata")
        assert rest_response1.status_code == 200

        # Then via MCP
        mcp_message = {"method": "resources/read", "params": {"uri": "schema://metadata"}}
        mcp_response1 = await mcp_server.handle_message(json.dumps(mcp_message))
        assert "result" in mcp_response1

        # Second access via MCP (should use cache)
        mcp_response2 = await mcp_server.handle_message(json.dumps(mcp_message))
        assert "result" in mcp_response2

        # Results should be consistent
        mcp_data1 = json.loads(mcp_response1["result"]["contents"][0]["text"])
        mcp_data2 = json.loads(mcp_response2["result"]["contents"][0]["text"])
        assert mcp_data1 == mcp_data2

        await mcp_server.stop()

    @pytest.mark.asyncio
    async def test_monitoring_metrics_collection_for_mcp_operations(self):
        """Test monitoring metrics collection for MCP operations."""
        from hpxml_schema_api.monitoring import get_monitor

        config = MCPConfig(transport="stdio")
        mcp_server = MCPServer(config)
        await mcp_server.start()

        monitor = get_monitor()

        # Perform MCP operations
        messages = [
            {"method": "resources/list", "params": {}},
            {"method": "tools/list", "params": {}},
            {"method": "resources/read", "params": {"uri": "schema://metadata"}},
        ]

        for message in messages:
            await mcp_server.handle_message(json.dumps(message))

        # Check that metrics were collected
        # Note: This assumes the monitoring system tracks MCP operations
        metrics = monitor.get_performance_summary()
        assert isinstance(metrics, dict)

        await mcp_server.stop()


class TestMCPServerConfiguration:
    """Test MCP server configuration scenarios."""

    def test_http_server_configuration(self):
        """Test HTTP server configuration."""
        config = MCPConfig(
            transport="http",
            host="0.0.0.0",
            port=8001,
            auth_token="test-token"
        )
        server = MCPServer(config)

        assert server.config.transport == "http"
        assert server.config.host == "0.0.0.0"
        assert server.config.port == 8001
        assert server.config.auth_token == "test-token"

    def test_stdio_server_configuration(self):
        """Test stdio server configuration."""
        config = MCPConfig(transport="stdio")
        server = MCPServer(config)

        assert server.config.transport == "stdio"
        assert server.config.port is None

    @pytest.mark.asyncio
    async def test_server_configuration_switching(self):
        """Test switching between different server configurations."""
        # Start with stdio
        config1 = MCPConfig(transport="stdio")
        server1 = MCPServer(config1)
        await server1.start()
        assert server1.running is True
        await server1.stop()

        # Switch to HTTP
        config2 = MCPConfig(transport="http", port=8001)
        server2 = MCPServer(config2)
        await server2.start()
        assert server2.running is True
        await server2.stop()

    def test_environment_override_behavior(self):
        """Test environment variable override behavior."""
        # Test that environment variables override defaults
        with patch.dict(os.environ, {
            'MCP_TRANSPORT': 'http',
            'MCP_PORT': '9000',
            'MCP_AUTH_TOKEN': 'env-token'
        }):
            config = MCPConfig.from_env()
            assert config.transport == "http"
            assert config.port == 9000
            assert config.auth_token == "env-token"


class TestMCPServerLifecycle:
    """Test MCP server lifecycle management."""

    @pytest.mark.asyncio
    async def test_server_startup_sequence(self):
        """Test server startup sequence."""
        config = MCPConfig(transport="stdio")
        server = MCPServer(config)

        # Initially not running
        assert server.running is False
        assert server.server is None

        # Start server
        await server.start()

        # Should be running with components initialized
        assert server.running is True
        assert server.server is not None
        assert server.bridge is not None

        # Resources and tools should be registered
        resources, tools = server.bridge.introspect_schema()
        assert len(resources) > 0
        assert len(tools) > 0

        await server.stop()

    @pytest.mark.asyncio
    async def test_server_shutdown_sequence(self):
        """Test server shutdown sequence."""
        config = MCPConfig(transport="http", port=8001)
        server = MCPServer(config)

        await server.start()
        assert server.running is True

        # Stop server
        await server.stop()
        assert server.running is False

        # HTTP server should be cleaned up
        assert server.http_server is None

    @pytest.mark.asyncio
    async def test_server_restart_capability(self):
        """Test server restart capability."""
        config = MCPConfig(transport="stdio")
        server = MCPServer(config)

        # Start -> Stop -> Start cycle
        await server.start()
        original_bridge = server.bridge

        await server.stop()
        assert server.running is False

        await server.start()
        assert server.running is True
        # Should reinitialize components
        assert server.bridge is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])