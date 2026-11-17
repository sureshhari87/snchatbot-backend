"""Browser Origin restriction independent of upstream CORS response headers.
This does not authenticate callers. Requests without Origin retain normal auth.
"""

import ipaddress
from urllib.parse import urlsplit

BROWSER_CORS_METHODS = ("DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT")
_METHODS = frozenset(BROWSER_CORS_METHODS)


class BrowserOriginGuard:
    def __init__(self, app, allow_origins, *, allow_insecure_loopback=False):
        self.app = app
        origins = set()
        for origin in allow_origins:
            if not isinstance(origin, str) or len(origin) > 2048 or not origin.isascii():
                raise ValueError("Origin allowlist requires review")
            parsed = urlsplit(origin)
            try:
                _ = parsed.port
            except ValueError:
                raise ValueError("Origin allowlist requires review") from None
            try:
                loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
            except ValueError:
                loopback = parsed.hostname == "localhost"
            allowed_scheme = parsed.scheme == "https" or (
                allow_insecure_loopback and parsed.scheme == "http" and loopback
            )
            if (
                not allowed_scheme
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in origin
                or any(c.isspace() for c in origin)
            ):
                raise ValueError("Origin allowlist requires review")
            origins.add(origin.encode("ascii"))
        self.allow_origins = frozenset(origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        origins = [v for k, v in scope.get("headers", []) if k.lower() == b"origin"]
        if origins:
            if len(origins) != 1 or origins[0] not in self.allow_origins:
                await self.reject(scope, send)
                return
            if scope.get("method") == "OPTIONS":
                requested = [
                    v
                    for k, v in scope.get("headers", [])
                    if k.lower() == b"access-control-request-method"
                ]
                if requested and (
                    len(requested) != 1
                    or requested[0].decode("ascii", errors="replace") not in _METHODS
                ):
                    await self.reject(scope, send)
                    return
        await self.app(scope, receive, send)

    @staticmethod
    async def reject(scope, send):
        body = b'{"detail":"Origin not allowed"}'
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send(
            {"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else body}
        )
