"""
FastAPI integration for MCP server.

This module provides functionality to mount MCP server endpoints on FastAPI applications,
enabling dual-mode operation where the same app serves both REST/GraphQL and MCP protocols.
"""

import json
import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from .graphql_bridge import GraphQLMCPBridge
from .graphql_schema import schema as graphql_schema
from .mcp_server import MCPConfig, MCPServer

logger = logging.getLogger(__name__)


class MCPFastAPIIntegration:
    """Mount an MCP (Model Context Protocol) surface inside a FastAPI app.

    Responsibilities:
        * Parse and validate incoming MCP JSON-RPC style messages over HTTP.
        * Delegate resource & tool operations to a :class:`GraphQLMCPBridge`.
        * Enforce optional bearer token auth (lightweight middleware pattern).

    Thread-safety: FastAPI instantiates this once per application; internal
    state is read-mostly so no additional locking is required.
    """

    def __init__(self, mcp_config: Optional[MCPConfig] = None):
        """Initialize MCP FastAPI integration.

        Args:
            mcp_config: Optional MCP configuration. If None, uses defaults.
        """
        self.mcp_config = mcp_config or MCPConfig(transport="http")
        self.bridge = GraphQLMCPBridge(graphql_schema)
        logger.info("MCP FastAPI integration initialized")

    async def handle_mcp_request(self, request: Request) -> JSONResponse:
        """Handle a single MCP HTTP POST request.

        Workflow:
            1. Decode JSON body with defensive error handling.
            2. Optionally validate bearer token if auth is enabled.
            3. Forward the message payload to the GraphQL bridge for
               translation into schema/tool operations.
            4. Wrap response / errors into JSON-RPC style result envelope.

        Returns:
            fastapi.responses.JSONResponse: Structured protocol response.
        """
        try:
            # Parse JSON body
            body = await request.body()
            if not body:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": {
                            "code": -32700,
                            "message": "Parse error: Empty request body",
                        }
                    },
                )

            try:
                message = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError as e:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": {"code": -32700, "message": f"Parse error: {str(e)}"}
                    },
                )

            # Handle authentication if required
            auth_token = None
            if self.mcp_config.require_auth:
                auth_header = request.headers.get("Authorization")
                if auth_header and auth_header.startswith("Bearer "):
                    auth_token = auth_header[7:]  # Remove "Bearer " prefix

                if not auth_token or auth_token != self.mcp_config.auth_token:
                    return JSONResponse(
                        status_code=401,
                        content={
                            "error": {
                                "code": -32600,
                                "message": "Authentication required",
                            }
                        },
                    )

            # Process MCP message
            response = await self.bridge.handle_mcp_message(message)

            return JSONResponse(content=response)

        except Exception as e:
            logger.error(f"Error handling MCP request: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": {"code": -32603, "message": f"Internal error: {str(e)}"}
                },
            )

    def create_mcp_routes(self) -> list[APIRoute]:
        """Create raw APIRoute objects (alternate mounting strategy).

        Typically you should call :func:`mount_mcp_server` instead; this helper
        exists for advanced composition where the caller manually extends the
        route table before FastAPI instantiation.
        """
        routes = [
            APIRoute(
                path="",
                endpoint=self.handle_mcp_request,
                methods=["POST"],
                name="mcp_endpoint",
            ),
            APIRoute(
                path="/",
                endpoint=self.handle_mcp_request,
                methods=["POST"],
                name="mcp_endpoint_slash",
            ),
        ]
        return routes


def mount_mcp_server(
    app: FastAPI, path: str = "/mcp", config: Optional[MCPConfig] = None
) -> None:
    """Mount MCP endpoints under a given path.

    Adds three endpoint groups:
        POST {path}[/]           -> primary MCP message handler
        GET  {path}/health       -> lightweight health probe
        GET  {path}/info         -> introspection summary

    Args:
        app: FastAPI application instance.
        path: Base path for MCP endpoints (default "/mcp").
        config: Optional :class:`MCPConfig` instance.
    """
    logger.info(f"Mounting MCP server at {path}")

    # Create MCP integration
    mcp_integration = MCPFastAPIIntegration(config)

    # Add the MCP endpoint
    @app.post(path)
    @app.post(f"{path}/")
    async def mcp_endpoint(request: Request) -> JSONResponse:
        """Primary MCP POST handler (supports both path variants)."""
        return await mcp_integration.handle_mcp_request(request)

    # Add health check for MCP
    @app.get(f"{path}/health")
    async def mcp_health() -> Dict[str, Any]:
        """Simple liveness probe for orchestration systems."""
        return {
            "status": "ok",
            "service": "mcp-server",
            "version": "1.0.0",
            "transport": "http",
        }

    # Add info endpoint
    @app.get(f"{path}/info")
    async def mcp_info() -> Dict[str, Any]:
        """Summarize exported MCP resources/tools for discovery."""
        resources, tools = mcp_integration.bridge.introspect_schema()

        return {
            "service": "hpxml-schema-api-mcp",
            "version": "1.0.0",
            "protocol": "Model Context Protocol",
            "resources": len(resources),
            "tools": len(tools),
            "transport": "http",
        }

    logger.info(f"MCP server mounted successfully at {path}")


def create_mcp_app(config: Optional[MCPConfig] = None) -> FastAPI:
    """Create a self-contained MCP FastAPI app.

    Useful for running an isolated MCP service (e.g., separate from the core
    REST + GraphQL API) for security or scaling reasons.

    Args:
        config: Optional :class:`MCPConfig`.

    Returns:
        FastAPI: Configured application instance with MCP routes at root.
    """
    app = FastAPI(
        title="HPXML Schema API - MCP Server",
        description="Model Context Protocol server for HPXML Schema API",
        version="1.0.0",
    )

    # Mount MCP server at root
    mount_mcp_server(app, path="", config=config)

    return app


async def run_mcp_fastapi_server(
    config: Optional[MCPConfig] = None, host: str = "0.0.0.0", port: int = 8001
) -> None:
    """Start an MCP FastAPI server with uvicorn programmatically.

    This convenience wrapper is used mostly in tests / ad‑hoc runs. For
    production deployment prefer invoking uvicorn directly with process
    supervision.

    Args:
        config: Optional :class:`MCPConfig`.
        host: Bind address (default 0.0.0.0).
        port: TCP port (default 8001).
    """
    import uvicorn

    app = create_mcp_app(config)

    logger.info(f"Starting MCP FastAPI server on {host}:{port}")

    uvicorn_config = uvicorn.Config(app=app, host=host, port=port, log_level="info")

    server = uvicorn.Server(uvicorn_config)
    await server.serve()
