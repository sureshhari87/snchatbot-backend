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


def request_json(path: str) -> tuple[int, dict]:
    request = urllib.request.Request(f"{BASE_URL}{path}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body)


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
