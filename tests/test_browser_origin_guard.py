import unittest
from unittest.mock import patch

import httpx
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from browser_origin_guard import BROWSER_CORS_METHODS, BrowserOriginGuard

ADMIN = "https://sona-jewellery-admin.web.app"
PUBLIC = "https://sona-jewellery-app.web.app"
EVIL = "https://not-approved.invalid"
AUTH = "Bearer SYNTHETIC_TEST_ONLY"


class ReflectingProxy:
    def __init__(self, app, intercept=False):
        self.app = app
        self.intercept = intercept

    async def __call__(self, scope, receive, send):
        origin = next((v for k, v in scope["headers"] if k == b"origin"), None)
        if self.intercept and scope["method"] == "OPTIONS":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"access-control-allow-origin", origin or b"*")],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        async def reflected(message):
            if message["type"] == "http.response.start" and origin:
                message = {
                    **message,
                    "headers": [
                        (k, v) for k, v in message["headers"] if k != b"access-control-allow-origin"
                    ]
                    + [(b"access-control-allow-origin", origin)],
                }
            await send(message)

        await self.app(scope, receive, reflected)


class GuardTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self, origins=None, proxy=False, intercept=False):
        self.calls = []

        async def endpoint(request):
            self.calls.append(request.url.path)
            if request.headers.get("authorization") != AUTH:
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
            if request.method == "POST":
                await request.body()
            return JSONResponse({"authenticated_handler_ran": True})

        inner = Starlette(
            routes=[
                Route("/private", endpoint, methods=["GET", "POST"]),
                Mount("/sona", Starlette(routes=[Route("/chat", endpoint, methods=["POST"])])),
            ]
        )
        cors = CORSMiddleware(
            inner,
            allow_origins=[ADMIN, PUBLIC],
            allow_credentials=True,
            allow_headers=["*"],
            allow_methods=BROWSER_CORS_METHODS,
        )
        app = BrowserOriginGuard(cors, [ADMIN, PUBLIC] if origins is None else origins)
        return ReflectingProxy(app, intercept) if proxy else app

    async def request(self, app, method="GET", path="/private", headers=None, body=None):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://api.invalid"
        ) as c:
            return await c.request(method, path, headers=headers, content=body)

    async def test_advertised_methods_match_guard_after_framework_expands_wildcard(self):
        future_methods = (*BROWSER_CORS_METHODS, "QUERY")
        with patch("starlette.middleware.cors.ALL_METHODS", future_methods):
            app = self.make_app()
            for method in BROWSER_CORS_METHODS:
                response = await self.request(
                    app,
                    method="OPTIONS",
                    headers={
                        "Origin": ADMIN,
                        "Access-Control-Request-Method": method,
                        "Access-Control-Request-Headers": "authorization,content-type",
                    },
                )
                self.assertEqual(response.status_code, 200)
                advertised = {
                    value.strip()
                    for value in response.headers["access-control-allow-methods"].split(",")
                }
                self.assertEqual(advertised, set(BROWSER_CORS_METHODS))
                self.assertEqual(response.headers["access-control-allow-origin"], ADMIN)
            self.assertEqual(self.calls, [])

    async def test_framework_query_preflight_is_rejected_before_handlers(self):
        with patch("starlette.middleware.cors.ALL_METHODS", (*BROWSER_CORS_METHODS, "QUERY")):
            response = await self.request(
                self.make_app(),
                method="OPTIONS",
                headers={"Origin": ADMIN, "Access-Control-Request-Method": "QUERY"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("access-control-allow-origin", response.headers)
        self.assertEqual(self.calls, [])

    async def test_approved_origin_does_not_bypass_authentication(self):
        r = await self.request(self.make_app(), headers={"Origin": ADMIN})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers["access-control-allow-origin"], ADMIN)

    async def test_both_approved_origins_reach_authenticated_handler(self):
        for origin in [ADMIN, PUBLIC]:
            r = await self.request(
                self.make_app(), headers={"Origin": origin, "Authorization": AUTH}
            )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.headers["access-control-allow-origin"], origin)

    async def test_unapproved_origin_is_rejected_before_private_handler(self):
        r = await self.request(self.make_app(), headers={"Origin": EVIL, "Authorization": AUTH})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.calls, [])
        self.assertNotIn("access-control-allow-origin", r.headers)
        self.assertNotIn("authenticated_handler_ran", r.text)

    async def test_rejected_post_does_not_read_body_or_run_handler(self):
        app = self.make_app()
        sent = []

        async def receive():
            raise AssertionError("Denied body must not be consumed")

        async def send(message):
            sent.append(message)

        await app(
            {
                "type": "http",
                "method": "POST",
                "headers": [(b"origin", EVIL.encode()), (b"authorization", AUTH.encode())],
            },
            receive,
            send,
        )
        self.assertEqual(sent[0]["status"], 403)
        self.assertEqual(self.calls, [])

    async def test_missing_origin_retains_native_client_authentication(self):
        app = self.make_app()
        r = await self.request(app)
        self.assertEqual(r.status_code, 401)
        r = await self.request(app, headers={"Authorization": AUTH})
        self.assertEqual(r.status_code, 200)

    async def test_empty_allowlist_denies_browser_origin(self):
        r = await self.request(
            self.make_app(origins=[]), headers={"Origin": ADMIN, "Authorization": AUTH}
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.calls, [])

    async def test_null_empty_suffix_and_path_origins_denied(self):
        for origin in [
            "null",
            "",
            ADMIN + ".attacker.invalid",
            ADMIN + "/",
            ADMIN + "/path",
            ADMIN + ", " + EVIL,
        ]:
            r = await self.request(
                self.make_app(), headers={"Origin": origin, "Authorization": AUTH}
            )
            self.assertEqual(r.status_code, 403)
            self.assertEqual(self.calls, [])

    async def test_duplicate_origin_headers_denied(self):
        r = await self.request(
            self.make_app(), headers=[("Origin", ADMIN), ("Origin", EVIL), ("Authorization", AUTH)]
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.calls, [])

    async def test_approved_preflight_retains_CORS_contract(self):
        r = await self.request(
            self.make_app(),
            method="OPTIONS",
            headers={
                "Origin": ADMIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["access-control-allow-origin"], ADMIN)
        self.assertEqual(self.calls, [])

    async def test_denied_preflight_not_allowed(self):
        r = await self.request(
            self.make_app(),
            method="OPTIONS",
            headers={"Origin": EVIL, "Access-Control-Request-Method": "POST"},
        )
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("access-control-allow-origin", r.headers)
        self.assertEqual(self.calls, [])

    async def test_invalid_method_approved_origin_preflight_rejected(self):
        r = await self.request(
            self.make_app(),
            method="OPTIONS",
            headers={"Origin": ADMIN, "Access-Control-Request-Method": "SONA_PROBE"},
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.calls, [])

    async def test_duplicate_requested_methods_rejected(self):
        r = await self.request(
            self.make_app(),
            method="OPTIONS",
            headers=[
                ("Origin", ADMIN),
                ("Access-Control-Request-Method", "GET"),
                ("Access-Control-Request-Method", "POST"),
            ],
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.calls, [])

    async def test_mounted_AI_route_is_also_protected(self):
        r = await self.request(
            self.make_app(),
            method="POST",
            path="/sona/chat",
            headers={"Origin": EVIL, "Authorization": AUTH},
            body=b"{}",
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.calls, [])

    async def test_reflected_edge_header_does_not_expose_private_response(self):
        r = await self.request(
            self.make_app(proxy=True), headers={"Origin": EVIL, "Authorization": AUTH}
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.headers["access-control-allow-origin"], EVIL)
        self.assertNotIn("authenticated_handler_ran", r.text)
        self.assertEqual(self.calls, [])

    async def test_proxy_intercepted_preflight_remains_an_external_gate(self):
        r = await self.request(
            self.make_app(proxy=True, intercept=True),
            method="OPTIONS",
            headers={"Origin": EVIL, "Access-Control-Request-Method": "SONA_PROBE"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["access-control-allow-origin"], EVIL)
        self.assertEqual(self.calls, [])

    def test_unsafe_configuration_rejected(self):
        for origin in [
            "*",
            "http://example.com",
            "https://user:pass@example.com",
            "https://example.com/path",
            "https://example.com?x=1",
            "https://example.com#fragment",
        ]:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                BrowserOriginGuard(None, [origin])

    def test_HTTP_loopback_configuration_is_staging_only(self):
        for origin in ["http://127.0.0.1:7860", "http://localhost:7860", "http://[::1]:7860"]:
            guard = BrowserOriginGuard(None, [origin], allow_insecure_loopback=True)
            self.assertIn(origin.encode(), guard.allow_origins)
            with self.assertRaises(ValueError):
                BrowserOriginGuard(None, [origin])

    def test_staging_exception_does_not_allow_external_HTTP(self):
        for origin in [
            "http://example.com",
            "http://127.0.0.1.attacker.invalid",
            "http://192.168.1.1",
        ]:
            with self.assertRaises(ValueError):
                BrowserOriginGuard(None, [origin], allow_insecure_loopback=True)

    async def test_staging_HTTP_loopback_preflight_with_real_CORS_stack(self):
        origin = "http://127.0.0.1:7860"
        app = BrowserOriginGuard(
            CORSMiddleware(
                Starlette(), allow_origins=[origin], allow_methods=["*"], allow_headers=["*"]
            ),
            [origin],
            allow_insecure_loopback=True,
        )
        response = await self.request(
            app,
            method="OPTIONS",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], origin)


if __name__ == "__main__":
    unittest.main()
