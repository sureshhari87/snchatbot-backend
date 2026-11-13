"""Read-only deployed contract checks and sanitized upstream failure reporting."""

import io
from copy import deepcopy
from urllib.error import HTTPError

import pytest

from main import app
from scripts import smoke_test as smoke


@pytest.fixture
def schema():
    return deepcopy(app.openapi())


def test_release_checker_accepts_backend_contract(schema):
    smoke.check_flutter_release_contract(schema)


@pytest.mark.parametrize("missing", ["path", "method", "scheme"])
def test_release_checker_rejects_missing_contract_parts(schema, missing):
    path = "/users/me/customer-profile"
    if missing == "path":
        del schema["paths"][path]
    elif missing == "method":
        del schema["paths"][path]["patch"]
    else:
        schema["components"]["securitySchemes"].clear()
    with pytest.raises(smoke.SmokeTestError):
        smoke.check_flutter_release_contract(schema)


@pytest.mark.parametrize("security", [None, [], [{}], [{"OAuth2PasswordBearer": []}, {}]])
def test_release_checker_rejects_anonymous_auth_alternatives(schema, security):
    schema["paths"]["/users/me/customer-profile"]["get"]["security"] = security
    with pytest.raises(smoke.SmokeTestError, match="permits anonymous access"):
        smoke.check_flutter_release_contract(schema)


def test_release_checker_accepts_inherited_global_auth(schema):
    operation = schema["paths"]["/users/me/customer-profile"]["get"]
    schema["security"] = operation.pop("security")
    smoke.check_flutter_release_contract(schema)


@pytest.mark.parametrize("status", [200, 502])
def test_non_json_upstream_error_does_not_expose_body(monkeypatch, status):
    def open_url(request, **kwargs):
        body = io.BytesIO(b"<html>private upstream diagnostics</html>")
        if status == 502:
            raise HTTPError(request.full_url, status, "Bad Gateway", {}, body)
        body.status = status
        return body

    monkeypatch.setattr(smoke, "BASE_URL", "https://staging.example")
    monkeypatch.setattr(smoke.urllib.request, "urlopen", open_url)
    with pytest.raises(smoke.SmokeTestError, match=f"HTTP {status} with non-JSON") as error:
        smoke.request_json("/health")
    assert "private upstream diagnostics" not in str(error.value)


def test_json_http_error_is_returned_without_hiding_status(monkeypatch):
    def open_url(request, **kwargs):
        assert request.get_method() == "GET"
        assert kwargs["timeout"] == smoke.TIMEOUT_SECONDS
        raise HTTPError(
            request.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"detail":"Not authenticated"}')
        )

    monkeypatch.setattr(smoke, "BASE_URL", "https://staging.example")
    monkeypatch.setattr(smoke.urllib.request, "urlopen", open_url)
    assert smoke.request_json("/admin/capabilities") == (401, {"detail": "Not authenticated"})


def test_non_object_json_is_rejected(monkeypatch):
    response = io.BytesIO(b"[]")
    response.status = 200
    monkeypatch.setattr(smoke, "BASE_URL", "https://staging.example")
    monkeypatch.setattr(smoke.urllib.request, "urlopen", lambda *args, **kwargs: response)
    with pytest.raises(smoke.SmokeTestError, match="without a JSON object"):
        smoke.request_json("/ready")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@staging.example",
        "https://staging.example?key=secret",
        "https://staging.example#token",
    ],
)
def test_base_url_rejects_embedded_secrets(monkeypatch, url):
    monkeypatch.setattr(smoke, "BASE_URL", url)
    with pytest.raises(smoke.SmokeTestError, match="must not include credentials"):
        smoke.validate_base_url()


@pytest.mark.parametrize("authorized_without_token", [False, True])
def test_deployed_release_probe_checks_unauthenticated_boundaries(
    monkeypatch, schema, authorized_without_token
):
    calls = []

    def read(path):
        calls.append(path)
        if path == "/openapi.json":
            return 200, schema
        return (200 if authorized_without_token else 401), {}

    monkeypatch.setattr(smoke, "request_json", read)
    monkeypatch.setattr(smoke, "EXPECT_LATEST_CHAT_CONTRACT", False)
    monkeypatch.setattr(smoke, "EXPECT_COMMERCE_CONTRACT", False)
    monkeypatch.setattr(smoke, "EXPECT_FLUTTER_RELEASE_CONTRACT", True)
    if authorized_without_token:
        with pytest.raises(smoke.SmokeTestError, match="must return 401"):
            smoke.check_openapi_contract()
    else:
        smoke.check_openapi_contract()
        assert calls == ["/openapi.json", "/admin/capabilities", "/notifications/unread-count"]
