import json
import os
import time
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse


class SmokeTestError(AssertionError):
    pass


BASE_URL = (os.getenv("SMOKE_BASE_URL") or os.getenv("LIVE_API_BASE_URL") or "").rstrip("/")
TIMEOUT_SECONDS = float(os.getenv("SMOKE_TIMEOUT_SECONDS", "30"))
RETRIES = int(os.getenv("SMOKE_RETRIES", "6"))
RETRY_DELAY_SECONDS = float(os.getenv("SMOKE_RETRY_DELAY_SECONDS", "5"))
EXPECT_LATEST_CHAT_CONTRACT = os.getenv(
    "SMOKE_EXPECT_LATEST_CHAT_CONTRACT",
    os.getenv("LIVE_API_EXPECT_LATEST_CHAT_CONTRACT", ""),
).lower() in {"1", "true", "yes", "on"}
EXPECT_COMMERCE_CONTRACT = os.getenv("SMOKE_EXPECT_COMMERCE_CONTRACT", "0") == "1"
EXPECT_FLUTTER_RELEASE_CONTRACT = os.getenv("SMOKE_EXPECT_FLUTTER_RELEASE_CONTRACT", "0") == "1"

FLUTTER_RELEASE_ROUTES = {
    "/admin/capabilities": {"get"},
    "/users/me/customer-profile": {"get", "patch"},
    "/users/me/customer-profile/email/request": {"post"},
    "/users/me/customer-profile/email/verify": {"post"},
    "/notifications/unread-count": {"get"},
    "/billing/payments/{source}/{source_id}/receipt": {"post"},
    "/billing/savings/payments/{payment_id}/receipt": {"post"},
    "/billing/sales/{source}/{source_id}": {"get"},
    "/billing/documents/{document_id}/pdf-data": {"get"},
    "/purchase-identity": {"get", "post"},
    "/admin/purchase-identities/{identity_id}": {"get"},
    "/admin/purchase-identities/{identity_id}/review": {"post"},
    "/orders/{order_id}/cancellation-review": {"get", "post"},
    "/orders/{order_id}/cancellation-review/decision": {"post"},
    "/admin/orders/{order_id}/cancellation-review": {"get"},
}


def check_flutter_release_contract(schema: dict) -> None:
    paths = schema.get("paths", {})
    schemes = schema.get("components", {}).get("securitySchemes", {})
    for path, methods in FLUTTER_RELEASE_ROUTES.items():
        require(path in paths, f"Flutter release contract is missing {path}")
        for method in methods:
            label = f"{method.upper()} {path}"
            operation = paths[path].get(method)
            require(isinstance(operation, dict), f"Flutter release contract is missing {label}")
            security = operation.get("security", schema.get("security", []))
            require(
                isinstance(security, list)
                and bool(security)
                and all(isinstance(option, dict) and bool(option) for option in security),
                f"{label} permits anonymous access in OpenAPI",
            )
            require(
                all(name in schemes for option in security for name in option),
                f"{label} references an undeclared authentication scheme",
            )


def check_commerce_contract(schema: dict) -> None:
    paths = schema.get("paths", {})
    for path in [
        "/products",
        "/cart",
        "/delivery/quote",
        "/admin/catalogue/products",
        "/admin/catalogue/rates",
        "/catalogue/metal-rates",
    ]:
        require(path in paths, f"OpenAPI contract is missing {path}")
    product = schema.get("components", {}).get("schemas", {}).get("ProductOut", {})
    require("attributes" in product.get("properties", {}), "ProductOut lacks imported attributes")


def request_json(path: str) -> tuple[int, dict]:
    request = urllib.request.Request(f"{BASE_URL}{path}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310
            status, body = response.status, response.read()
    except HTTPError as exc:
        status, body = exc.code, exc.read()
        exc.close()
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SmokeTestError(f"{path} returned HTTP {status} with non-JSON content") from None
    require(isinstance(data, dict), f"{path} returned HTTP {status} without a JSON object")
    return status, data


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeTestError(message)


def wait_for_health() -> None:
    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            status, body = request_json("/health")
            require(status == 200, f"/health returned {status}")
            require(body.get("status") == "ok", "/health did not return status=ok")
            return
        except (SmokeTestError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)

    raise SmokeTestError(f"Service did not become healthy: {last_error}") from last_error


def validate_base_url() -> None:
    parsed = urlparse(BASE_URL)
    require(parsed.scheme in {"http", "https"} and parsed.netloc, "SMOKE_BASE_URL must be http(s)")
    require(
        not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
        "SMOKE_BASE_URL must not include credentials, query parameters or fragments",
    )


def check_readiness() -> None:
    status, body = request_json("/ready")
    require(status == 200, f"/ready returned {status}: {body}")
    require(body.get("status") == "ok", "/ready did not return status=ok")
    database = body.get("dependencies", {}).get("database", {})
    require(database.get("status") == "ok", "database dependency is not ok")


def check_openapi_contract() -> None:
    status, schema = request_json("/openapi.json")
    require(status == 200, f"/openapi.json returned {status}")

    paths = schema.get("paths", {})
    for path in ["/health", "/ready", "/register", "/login", "/refresh", "/me", "/chat"]:
        require(path in paths, f"OpenAPI contract is missing {path}")

    chat_response = schema.get("components", {}).get("schemas", {}).get("ChatResponse", {})
    chat_properties = chat_response.get("properties", {})
    require("suggestions" in chat_properties, "ChatResponse is missing suggestions")

    if EXPECT_LATEST_CHAT_CONTRACT:
        for field in ["applied_filters", "result_count", "suggested_next_questions"]:
            require(field in chat_properties, f"ChatResponse is missing {field}")
    if EXPECT_COMMERCE_CONTRACT:
        check_commerce_contract(schema)
        status, _ = request_json("/cart")
        require(status == 401, "Unauthenticated cart access must return 401")
    if EXPECT_FLUTTER_RELEASE_CONTRACT:
        check_flutter_release_contract(schema)
        for path in ("/admin/capabilities", "/notifications/unread-count"):
            status, _ = request_json(path)
            require(status == 401, f"Unauthenticated {path} access must return 401")


def main() -> int:
    if not BASE_URL:
        print("SMOKE_BASE_URL is required.")
        return 2

    validate_base_url()
    wait_for_health()
    check_readiness()
    check_openapi_contract()
    print(f"Smoke tests passed for {BASE_URL}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
