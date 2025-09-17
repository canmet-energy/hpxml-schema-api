"""Model Context Protocol (MCP) server implementation.

Provides a thin server abstraction exposing HPXML schema exploration and
validation primitives as MCP resources & tools. Two transports are
supported: ``stdio`` (CLI / LSP‑like embedding) and a lightweight
HTTP façade (when FastAPI integration is present).

High-level responsibilities:
    * Bootstrap GraphQL-to-MCP bridge (:class:`GraphQLMCPBridge`).
    * Introspect schema to register resource + tool handlers lazily.
    * Dispatch JSON-RPC style MCP messages with optional bearer auth.
    * Offer pluggable transport initialization.

Out-of-scope concerns (intentionally omitted): clustering, persistent
sessions, advanced authorization, and streaming content types beyond text.
"""

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

try:
    from mcp.server import Server
    from mcp.server.session import ServerSession
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        EmbeddedResource,
        ImageContent,
        Prompt,
        Resource,
        TextContent,
        Tool,
    )

    MCP_AVAILABLE = True
    MCP_IMPORT_ERROR = None
except ImportError as e:
    MCP_AVAILABLE = False
    MCP_IMPORT_ERROR = str(e)

from .graphql_bridge import GraphQLMCPBridge
from .graphql_schema import schema as graphql_schema
from .versioned_routes import _build_versions_payload

logger = logging.getLogger(__name__)


@dataclass
class MCPConfig:
    """Configuration container for :class:`MCPServer`.

    Attributes:
        transport: Transport backend ("stdio" or "http").
        port: TCP port for HTTP transport (ignored for stdio).
        host: Bind host for HTTP transport.
        auth_token: Optional bearer token for simple auth.
        require_auth: If True, reject unauthenticated requests.
        graphql_endpoint: External GraphQL endpoint (future use; currently local schema used).
        log_level: Python logging level name.
    """

    transport: str = "stdio"
    port: Optional[int] = None
    host: str = "localhost"
    auth_token: Optional[str] = None
    require_auth: bool = False
    graphql_endpoint: str = "http://localhost:8000/graphql"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "MCPConfig":
        """Create configuration from environment variables."""
        return cls(
            transport=os.getenv("MCP_TRANSPORT", "stdio"),
            port=int(os.getenv("MCP_PORT", "8001")) if os.getenv("MCP_PORT") else None,
            host=os.getenv("MCP_HOST", "localhost"),
            auth_token=os.getenv("MCP_AUTH_TOKEN"),
            require_auth=os.getenv("MCP_REQUIRE_AUTH", "false").lower() == "true",
            graphql_endpoint=os.getenv(
                "MCP_GRAPHQL_ENDPOINT", "http://localhost:8000/graphql"
            ),
            log_level=os.getenv("MCP_LOG_LEVEL", "INFO"),
        )


class MCPServer:
    """Runtime façade managing transport lifecycle and MCP handlers.

    A single instance encapsulates a configured transport and the
    GraphQL bridge. Handler registration is performed once during
    :meth:`start`; repeated calls are idempotent.
    """

    def __init__(self, config: MCPConfig):
        """Instantiate server (no network side-effects yet).

        Raises:
            ImportError: If MCP dependencies are missing.
            ValueError: For unsupported transport values.
        """
        if not MCP_AVAILABLE:
            raise ImportError(f"MCP dependencies not installed: {MCP_IMPORT_ERROR}")

        self.config = config
        self.running = False
        self.server: Optional[Server] = None
        self.bridge: Optional[GraphQLMCPBridge] = None
        self.http_server: Optional[Any] = None
        self.auth_middleware: Optional[Any] = None

        # Validate transport
        if config.transport not in ["stdio", "http"]:
            raise ValueError(f"Unsupported transport: {config.transport}")

        # Initialize components
        self._setup_logging()
        self._initialize_bridge()
        self._setup_authentication()

    def _setup_logging(self) -> None:
        """Setup logging configuration."""
        logging.basicConfig(
            level=getattr(logging, self.config.log_level.upper()),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

    def _initialize_bridge(self) -> None:
        """Initialize GraphQL-to-MCP bridge."""
        try:
            self.bridge = GraphQLMCPBridge(graphql_schema)
            logger.info("GraphQL-to-MCP bridge initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize GraphQL bridge: {e}")
            raise

    def _setup_authentication(self) -> None:
        """Setup authentication middleware if required."""
        if self.config.require_auth:
            self.auth_middleware = self._create_auth_middleware()
            logger.info("Authentication middleware configured")

    def _create_auth_middleware(self) -> Any:
        """Create authentication middleware."""

        # Simple token-based authentication
        class AuthMiddleware:
            def __init__(self, expected_token: str):
                self.expected_token = expected_token

            def authenticate(self, token: Optional[str]) -> bool:
                return token == self.expected_token

        return AuthMiddleware(self.config.auth_token)

    async def start(self) -> None:
        """Start the configured transport and register handlers.

        Safe to call multiple times; subsequent calls log a warning and
        return early. For HTTP transport this only records intended
        configuration (actual server start occurs in integration layer).
        """
        if self.running:
            logger.warning("Server is already running")
            return

        logger.info(f"Starting MCP server with {self.config.transport} transport")

        # Create MCP server instance
        self.server = Server("hpxml-schema-api")

        # Register handlers
        await self._register_handlers()

        if self.config.transport == "stdio":
            await self._start_stdio_server()
        elif self.config.transport == "http":
            await self._start_http_server()

        self.running = True
        logger.info("MCP server started successfully")

    async def stop(self) -> None:
        """Stop the server and release transport resources (best-effort)."""
        if not self.running:
            return

        logger.info("Stopping MCP server")

        if self.http_server:
            await self._stop_http_server()

        self.running = False
        logger.info("MCP server stopped")

    async def _register_handlers(self) -> None:
        """Introspect GraphQL schema and register MCP resource/tool handlers."""
        if not self.server or not self.bridge:
            raise RuntimeError("Server or bridge not initialized")

        # Get resources and tools from bridge
        resources, tools = self.bridge.introspect_schema()
        # Register aggregate resource handlers once (not per resource) if not already
        if not hasattr(self, "_list_resources_handler"):
            await self._register_resource_handlers()

        # Register aggregate tool handlers once
        if not hasattr(self, "_list_tools_handler"):
            await self._register_tool_handlers()

        logger.info(
            f"Registered resource + tool handlers exposing {len(resources)} resources and {len(tools)} tools"
        )

    async def _register_resource_handlers(self) -> None:
        """Register list/read handlers (once)."""

        async def list_resources() -> List[Resource]:  # type: ignore
            resources, _ = self.bridge.introspect_schema()
            resources.append(
                Resource(
                    uri="mcp://schema_versions",
                    name="Schema Versions",
                    description="Available HPXML schema versions and endpoints",
                    mimeType="application/json",
                )
            )
            return resources

        async def read_resource(uri: str) -> str:  # type: ignore
            if uri == "mcp://schema_versions":
                return json.dumps(_build_versions_payload())
            return await self.bridge.read_resource(uri)

        # store on self for dispatcher
        self._list_resources_handler = list_resources  # type: ignore
        self._read_resource_handler = read_resource  # type: ignore

    async def _register_tool_handlers(self) -> None:
        """Register list/call tool handlers (once)."""

        async def list_tools() -> List[Tool]:  # type: ignore
            _, tools = self.bridge.introspect_schema()
            return tools

        async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:  # type: ignore
            result = await self.bridge.call_tool(name, arguments)
            return [TextContent(type="text", text=str(result))]

        self._list_tools_handler = list_tools  # type: ignore
        self._call_tool_handler = call_tool  # type: ignore

    async def _start_stdio_server(self) -> None:
        """Prepare stdio transport (placeholder for real loop integration)."""
        # This would typically run the stdio server
        # For now, we'll simulate it
        logger.info("Stdio server transport configured")

    async def _start_http_server(self) -> None:
        """Configure HTTP transport (delegated to FastAPI integration)."""
        try:
            from .mcp_fastapi_integration import run_mcp_fastapi_server

            # Create HTTP server configuration
            self.http_server = {
                "host": self.config.host,
                "port": self.config.port or 8001,
                "config": self.config,
            }

            logger.info(
                f"HTTP server configured on {self.config.host}:{self.config.port or 8001}"
            )

            # Note: In a real implementation, you'd start the FastAPI server here
            # For testing purposes, we just configure it

        except ImportError:
            logger.warning(
                "FastAPI integration not available, HTTP server mode disabled"
            )
            self.http_server = {"status": "configured"}

    async def _stop_http_server(self) -> None:
        """Teardown HTTP transport bookkeeping (FastAPI stops externally)."""
        if self.http_server:
            logger.info("Stopping HTTP server")
            self.http_server = None
            logger.info("HTTP server stopped")

    async def handle_message(
        self, message: str, auth_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Decode, authenticate (optional), and dispatch a single MCP message.

        Args:
            message: Raw JSON string representing an MCP request.
            auth_token: Optional bearer token when auth is enforced.

        Returns:
            Dict[str, Any]: JSON-serializable response envelope.
        """
        # Authentication check
        if self.config.require_auth and self.auth_middleware:
            if not self.auth_middleware.authenticate(auth_token):
                return {"error": {"code": -32600, "message": "Authentication required"}}

        try:
            # Parse message
            msg = json.loads(message)
            method = msg.get("method")

            # Handle ping
            if method == "ping":
                return {"result": "pong"}

            # Native methods we registered via decorators
            if method in {"list_resources", "read_resource", "list_tools", "call_tool"}:
                try:
                    if method == "list_resources" and hasattr(
                        self, "_list_resources_handler"
                    ):
                        resources = await self._list_resources_handler()  # type: ignore
                        serialized = []
                        for r in resources:
                            d = r.model_dump()
                            # Ensure URI and any URL-like fields are plain strings
                            if "uri" in d:
                                d["uri"] = str(d["uri"])
                            serialized.append(d)
                        return {"result": serialized}
                    if method == "read_resource" and hasattr(
                        self, "_read_resource_handler"
                    ):
                        uri = msg.get("params", {}).get("uri")
                        content = await self._read_resource_handler(uri)  # type: ignore
                        return {"result": content}
                    if method == "list_tools" and hasattr(self, "_list_tools_handler"):
                        tools = await self._list_tools_handler()  # type: ignore
                        serialized = []
                        for t in tools:
                            d = t.model_dump()
                            serialized.append(d)
                        return {"result": serialized}
                    if method == "call_tool" and hasattr(self, "_call_tool_handler"):
                        name = msg.get("params", {}).get("name")
                        arguments = msg.get("params", {}).get("arguments", {})
                        output = await self._call_tool_handler(name, arguments)  # type: ignore
                        return {"result": [c.model_dump() for c in output]}
                except Exception as e:
                    logger.error(f"Internal handler error: {e}")
                    return {
                        "error": {"code": -32603, "message": "Handlers not available"}
                    }

            # Delegate anything else to bridge
            if self.bridge:
                return await self.bridge.handle_mcp_message(msg)

            return {"error": {"code": -32601, "message": f"Method not found: {method}"}}

        except json.JSONDecodeError:
            return {"error": {"code": -32700, "message": "Parse error"}}
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            return {"error": {"code": -32603, "message": f"Internal error: {str(e)}"}}


async def run_server(config: Optional[MCPConfig] = None) -> None:
    """Convenience coroutine to start a long-running MCP server loop."""
    if config is None:
        config = MCPConfig.from_env()

    server = MCPServer(config)

    try:
        await server.start()

        if config.transport == "stdio":
            # For stdio, we would typically run the stdio server here
            logger.info("MCP server running on stdio...")
            # Keep the server running
            while server.running:
                await asyncio.sleep(1)
        elif config.transport == "http":
            logger.info(f"MCP server running on http://{config.host}:{config.port}")
            # Keep the server running
            while server.running:
                await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.error(f"Server error: {e}")
        raise
    finally:
        await server.stop()


def main() -> None:
    """CLI entry point for running the server via ``python -m``."""
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("Server shutdown complete")
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
