"""ASGI entry point for Django plus the optional Streamable HTTP MCP server."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rearvue.settings")

from django.core.asgi import get_asgi_application  # noqa: E402
django_application = get_asgi_application()

from rvmcp.auth import MCPAuthenticationMiddleware  # noqa: E402
from rvmcp.server import application as mcp_application  # noqa: E402


class RearVueASGIApplication:
    def __init__(self, django_app, mcp_app):
        self.django_app = django_app
        self.mcp_app = MCPAuthenticationMiddleware(mcp_app)
        self.mcp_lifespan_app = mcp_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.mcp_lifespan_app(scope, receive, send)
            return
        path = scope.get("path", "")
        if scope["type"] in {"http", "websocket"} and (path == "/mcp" or path.startswith("/mcp/")):
            mcp_scope = dict(scope)
            mcp_scope["root_path"] = scope.get("root_path", "") + "/mcp"
            mcp_scope["path"] = path[4:] or "/"
            mcp_scope["raw_path"] = mcp_scope["path"].encode("utf-8")
            await self.mcp_app(mcp_scope, receive, send)
            return
        await self.django_app(scope, receive, send)


application = RearVueASGIApplication(django_application, mcp_application)
