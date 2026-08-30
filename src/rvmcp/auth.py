from contextvars import ContextVar

from asgiref.sync import sync_to_async
from django.conf import settings

from .services import authenticate_token


current_client_id = ContextVar("rvmcp_current_client_id", default=None)


async def _respond(send, status, body=b"", headers=None):
    response_headers = [
        (b"content-type", b"text/plain; charset=utf-8"),
        (b"cache-control", b"no-store"),
        (b"x-accel-buffering", b"no"),
    ]
    response_headers.extend(headers or [])
    await send({"type": "http.response.start", "status": status, "headers": response_headers})
    await send({"type": "http.response.body", "body": body})


class MCPAuthenticationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4404})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not settings.MCP_ENABLED:
            await _respond(send, 404, b"Not found")
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        origin = headers.get(b"origin")
        allowed_origins = {value.encode("utf-8") for value in settings.MCP_ALLOWED_ORIGINS}
        if origin is not None and origin not in allowed_origins:
            await _respond(send, 403, b"Origin is not allowed")
            return

        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, token = authorization.partition(" ")
        client = None
        if scheme.lower() == "bearer" and token:
            client = await sync_to_async(authenticate_token, thread_sensitive=True)(token)
        if client is None:
            await _respond(
                send,
                401,
                b"Bearer authentication required",
                headers=[(b"www-authenticate", b'Bearer realm="RearVue MCP"')],
            )
            return

        context_token = current_client_id.set(client.id)
        try:
            async def send_unbuffered(message):
                if message["type"] == "http.response.start":
                    headers = [
                        (name, value)
                        for name, value in message.get("headers", [])
                        if name.lower() not in {b"cache-control", b"x-accel-buffering"}
                    ]
                    headers.extend(
                        [
                            (b"cache-control", b"no-store"),
                            (b"x-accel-buffering", b"no"),
                        ]
                    )
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_unbuffered)
        finally:
            current_client_id.reset(context_token)
