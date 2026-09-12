import io
import json
import urllib.request
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import main
import safe_http


class Response(io.BytesIO):
    status = 200
    headers = {"Cache-Control": "max-age=120"}


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/a", "data:text/plain,x", "https://u:p@example.com"],
)
def test_integration_rejects_unsafe_urls_before_opening(monkeypatch, url):
    opener = Mock()
    monkeypatch.setattr(safe_http.urllib.request, "build_opener", opener)
    with pytest.raises(ValueError):
        safe_http.http_urlopen(urllib.request.Request(url))
    opener.assert_not_called()


def test_integration_redirects_cannot_leak_auth_or_downgrade():
    handler = safe_http.SameOriginRedirect()
    request = urllib.request.Request(
        "https://example.com/a", headers={"Authorization": "Bearer test"}
    )
    for target in ("https://other.example/a", "http://example.com/a", "file:///secret"):
        with pytest.raises(ValueError):
            handler.redirect_request(request, None, 302, "Found", {}, target)
    redirected = handler.redirect_request(request, None, 302, "Found", {}, "https://example.com/b")
    assert redirected.full_url == "https://example.com/b"


@pytest.mark.parametrize(
    "body,expected",
    [
        (b"", {}),
        (b"not-json", {"raw": "not-json"}),
        (b"[1]", {"data": [1]}),
        (b'{"ok":true}', {"ok": True}),
    ],
)
@pytest.mark.parametrize("transport", ["json", "form", "query"])
def test_http_transports_encode_requests_and_decode_responses(
    monkeypatch, body, expected, transport
):
    requests = []

    def open_request(request, timeout):
        requests.append(request)
        assert timeout == 7
        return Response(body)

    monkeypatch.setattr(main, "http_urlopen", open_request)
    if transport == "json":
        result = main.json_http_request("POST", "https://example.com", {"a": "b c"}, timeout=7)
        assert json.loads(requests[0].data) == {"a": "b c"}
    elif transport == "form":
        result = main.urlencoded_http_request("POST", "https://example.com", {"a": "b c"}, 7)
        assert requests[0].data == b"a=b+c"
    else:
        result = main.query_http_request("https://example.com?x=1", {"a": "b c"}, 7)
        assert requests[0].full_url.endswith("?x=1&a=b+c")
    assert result == (200, expected)


def test_firebase_cert_cache_refresh_and_invalid_response(monkeypatch):
    monkeypatch.setattr(main, "_FIREBASE_CERT_CACHE", {"certs": {}, "expires_at": 0})
    opener = Mock(side_effect=lambda *a, **k: Response(b'{"kid":"cert"}'))
    monkeypatch.setattr(main, "http_urlopen", opener)
    assert main.fetch_firebase_public_certs() == {"kid": "cert"}
    assert main.fetch_firebase_public_certs() == {"kid": "cert"}
    assert opener.call_count == 1
    main.fetch_firebase_public_certs(force_refresh=True)
    assert opener.call_count == 2
    opener.side_effect = lambda *a, **k: Response(b"[]")
    with pytest.raises(HTTPException) as exc:
        main.fetch_firebase_public_certs(force_refresh=True)
    assert exc.value.status_code == 503
    opener.side_effect = OSError("offline")
    with pytest.raises(HTTPException) as exc:
        main.fetch_firebase_public_certs(force_refresh=True)
    assert exc.value.status_code == 503
    assert main.firebase_cache_max_age("") == 3600
    assert main.firebase_cache_max_age("max-age=1") == 60


@pytest.fixture
def firebase_verifier(monkeypatch):
    monkeypatch.setattr(main, "FIREBASE_AUTH_ENABLED", True)
    monkeypatch.setattr(main, "FIREBASE_PROJECT_ID", "test-project")
    monkeypatch.setattr(main, "FIREBASE_REQUIRE_EMAIL_VERIFIED", True)
    monkeypatch.setattr(main.jwt, "get_unverified_header", lambda _: {"kid": "key"})
    monkeypatch.setattr(main, "fetch_firebase_public_certs", lambda **_: {"key": "certificate"})
    payload = {
        "aud": "test-project",
        "iss": "https://securetoken.google.com/test-project",
        "sub": "uid",
        "email": "test@example.com",
        "email_verified": True,
    }

    def decode(token, cert, **options):
        assert cert == "certificate"
        assert options == {
            "algorithms": ["RS256"],
            "audience": "test-project",
            "issuer": "https://securetoken.google.com/test-project",
        }
        return payload

    monkeypatch.setattr(main.jwt, "decode", decode)
    return payload


def test_firebase_verified_identity_and_phone_only_identity(firebase_verifier):
    assert main.verify_firebase_id_token("test-token")["sub"] == "uid"
    firebase_verifier.pop("email")
    firebase_verifier["phone_number"] = "+919876543210"
    assert main.verify_firebase_id_token("test-token")["phone_number"] == "+919876543210"


@pytest.mark.parametrize(
    "field,value,status",
    [
        ("aud", "wrong", 401),
        ("iss", "wrong", 401),
        ("sub", "", 401),
        ("email", "", 400),
        ("email_verified", False, 403),
    ],
)
def test_firebase_rejects_invalid_identity_claims(firebase_verifier, field, value, status):
    firebase_verifier[field] = value
    with pytest.raises(HTTPException) as exc:
        main.verify_firebase_id_token("test-token")
    assert exc.value.status_code == status


def test_firebase_rotated_key_refresh_and_missing_key(firebase_verifier, monkeypatch):
    fetch = Mock(side_effect=[{}, {"key": "certificate"}])
    monkeypatch.setattr(main, "fetch_firebase_public_certs", fetch)
    assert main.verify_firebase_id_token("test-token")["sub"] == "uid"
    assert fetch.call_args.kwargs == {"force_refresh": True}
    fetch.side_effect = [{}, {}]
    with pytest.raises(HTTPException) as exc:
        main.verify_firebase_id_token("test-token")
    assert exc.value.status_code == 401


def test_firebase_verifier_errors_are_fail_closed(firebase_verifier, monkeypatch):
    monkeypatch.setattr(main.jwt, "decode", Mock(side_effect=main.JWTError("bad signature")))
    with pytest.raises(HTTPException) as exc:
        main.verify_firebase_id_token("test-token")
    assert exc.value.status_code == 401
    monkeypatch.setattr(
        main.jwt, "get_unverified_header", Mock(side_effect=main.JWTError("bad header"))
    )
    with pytest.raises(HTTPException) as exc:
        main.verify_firebase_id_token("test-token")
    assert exc.value.status_code == 401
    monkeypatch.setattr(main, "FIREBASE_AUTH_ENABLED", False)
    with pytest.raises(HTTPException) as exc:
        main.verify_firebase_id_token("test-token")
    assert exc.value.status_code == 503


def test_http_opener_preserves_request_and_timeout(monkeypatch):
    opener = Mock()
    monkeypatch.setattr(safe_http.urllib.request, "build_opener", Mock(return_value=opener))
    request = urllib.request.Request("https://example.com/test")
    assert safe_http.http_urlopen(request, timeout=9) == opener.open.return_value
    opener.open.assert_called_once_with(request, timeout=9)


def test_firebase_real_rsa_certificate_signature(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-key")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    monkeypatch.setattr(main, "FIREBASE_AUTH_ENABLED", True)
    monkeypatch.setattr(main, "FIREBASE_PROJECT_ID", "test-project")
    monkeypatch.setattr(main, "FIREBASE_REQUIRE_EMAIL_VERIFIED", True)
    monkeypatch.setattr(main, "fetch_firebase_public_certs", lambda **_: {"test-key": pem})
    claims = {
        "sub": "uid",
        "aud": "test-project",
        "iss": "https://securetoken.google.com/test-project",
        "email": "test@example.com",
        "email_verified": True,
        "exp": now + timedelta(minutes=5),
    }
    token = main.jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})
    assert main.verify_firebase_id_token(token)["sub"] == "uid"
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    invalid = main.jwt.encode(claims, wrong_key, algorithm="RS256", headers={"kid": "test-key"})
    with pytest.raises(HTTPException) as exc:
        main.verify_firebase_id_token(invalid)
    assert exc.value.status_code == 401
