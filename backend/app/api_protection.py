import logging
import secrets
from hashlib import sha256
from ipaddress import ip_address

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)
MAX_JSON_BYTES = 512 * 1024


def is_loopback(client: tuple[str, int] | None) -> bool:
    try:
        return client is not None and ip_address(client[0]).is_loopback
    except ValueError:
        return False


class APIProtection:
    def __init__(self, app, token: str, origins: list[str]):
        self.app = app
        self.token_digest = sha256(f"Bearer {token}".encode()).digest() if token else None
        self.origins = set(origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        started = False

        async def send_private(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        async def reject(status: int, detail: str, headers=None):
            await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, send_private)

        headers = Headers(scope=scope)
        path = scope["path"]
        method = scope["method"]
        if self.token_digest is None and not is_loopback(scope.get("client")):
            await reject(403, "Token-free development access is available only on loopback.")
            return
        if path != "/api/health" and self.token_digest is not None:
            supplied = sha256(headers.get("Authorization", "").encode()).digest()
            if not secrets.compare_digest(self.token_digest, supplied):
                await reject(401, "Enter a valid workspace access token.", {"WWW-Authenticate": "Bearer"})
                return
        if headers.get("Origin") and headers["Origin"] not in self.origins:
            await reject(403, "This frontend origin is not allowed.")
            return

        receive_body = receive
        if method in {"POST", "PATCH", "PUT"} and path.rstrip("/") != "/api/resumes/upload":
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > MAX_JSON_BYTES:
                    await reject(413, "The request body is too large.")
                    return
                if not message.get("more_body", False):
                    break

            delivered = False
            async def receive_body():
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

        try:
            await self.app(scope, receive_body, send_private)
        except Exception as error:
            if started:
                raise
            logger.error("Unexpected API failure (%s).", type(error).__name__)
            await reject(500, "The request could not be completed. Please try again.")
