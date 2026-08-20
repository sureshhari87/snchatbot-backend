from models import PhoneAuthIdentity, PhoneOtpChallenge, RefreshToken, User


def enable_otp(monkeypatch, otp="123456"):
    import main

    sent_messages = []
    monkeypatch.setattr(main, "SMS_OTP_ENABLED", True)
    monkeypatch.setattr(main, "SMS_PROVIDER", "onhand")
    monkeypatch.setattr(main, "OTP_EXPIRE_MINUTES", 5)
    monkeypatch.setattr(main, "OTP_RESEND_COOLDOWN_SECONDS", 60)
    monkeypatch.setattr(main, "OTP_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(main, "PHONE_AUTH_PEPPER", "test-phone-pepper")
    monkeypatch.setattr(main, "generate_otp_code", lambda: otp)
    monkeypatch.setattr(
        main,
        "send_otp_sms",
        lambda phone, code: sent_messages.append((phone, code)) or True,
    )
    return sent_messages


def test_phone_otp_request_and_verify_creates_customer_session(client, db, monkeypatch):
    sent_messages = enable_otp(monkeypatch)

    request_response = client.post(
        "/auth/otp/request",
        json={"phone": "98765 43210"},
    )

    assert request_response.status_code == 200
    body = request_response.json()
    assert body["message"] == "OTP sent"
    assert body["phone_masked"].endswith("3210")
    assert sent_messages == [("+919876543210", "123456")]

    challenge = db.query(PhoneOtpChallenge).one()
    assert challenge.otp_hash != "123456"
    assert challenge.status == "sent"

    verify_response = client.post(
        "/auth/otp/verify",
        json={"phone": "+91 98765 43210", "otp": "123456"},
    )

    assert verify_response.status_code == 200
    tokens = verify_response.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]

    challenge = db.query(PhoneOtpChallenge).one()
    assert challenge.status == "verified"
    assert challenge.verified_at is not None

    user = db.query(User).filter(User.email.like("phone_%@phone.sona.invalid")).one()
    assert user.is_verified is True
    assert user.is_admin is False
    assert db.query(PhoneAuthIdentity).filter_by(user_id=user.id).count() == 1
    assert db.query(RefreshToken).filter_by(user_id=user.id).count() == 1

    me = client.get("/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == user.email


def test_phone_otp_reuses_existing_phone_identity(client, db, monkeypatch):
    enable_otp(monkeypatch)

    assert client.post("/auth/otp/request", json={"phone": "9876543210"}).status_code == 200
    assert (
        client.post(
            "/auth/otp/verify",
            json={"phone": "9876543210", "otp": "123456"},
        ).status_code
        == 200
    )

    assert client.post("/auth/otp/request", json={"phone": "+919876543210"}).status_code == 200
    assert (
        client.post(
            "/auth/otp/verify",
            json={"phone": "+919876543210", "otp": "123456"},
        ).status_code
        == 200
    )

    assert db.query(User).filter(User.email.like("phone_%@phone.sona.invalid")).count() == 1
    assert db.query(PhoneAuthIdentity).count() == 1


def test_phone_otp_resend_cooldown(client, monkeypatch):
    enable_otp(monkeypatch)

    first = client.post("/auth/otp/request", json={"phone": "9876543210"})
    second = client.post("/auth/otp/request", json={"phone": "9876543210"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert "wait" in second.json()["detail"].lower()


def test_phone_otp_invalid_attempts_lock_challenge(client, db, monkeypatch):
    enable_otp(monkeypatch)
    import main

    monkeypatch.setattr(main, "OTP_MAX_ATTEMPTS", 2)

    request_response = client.post("/auth/otp/request", json={"phone": "9876543210"})
    assert request_response.status_code == 200

    first = client.post("/auth/otp/verify", json={"phone": "9876543210", "otp": "000000"})
    second = client.post("/auth/otp/verify", json={"phone": "9876543210", "otp": "111111"})

    assert first.status_code == 401
    assert second.status_code == 401
    assert db.query(PhoneOtpChallenge).one().status == "locked"


def test_phone_otp_requires_enabled_service(client):
    response = client.post("/auth/otp/request", json={"phone": "9876543210"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Phone OTP login is not enabled"


def test_phone_otp_mobile_config_and_dependencies(client, monkeypatch):
    import main

    monkeypatch.setattr(main, "SMS_OTP_ENABLED", True)
    monkeypatch.setattr(main, "SMS_PROVIDER", "onhand")
    monkeypatch.setattr(main, "ONHANDSMS_API_URL", "https://sms.example.test/send")
    monkeypatch.setattr(main, "ONHANDSMS_API_KEY", "sms-key")
    monkeypatch.setattr(main, "ONHANDSMS_SENDER_ID", "SONAJW")

    config_response = client.get("/mobile/config")
    dependencies_response = client.get("/dependencies")

    assert config_response.status_code == 200
    assert config_response.json()["capabilities"]["phone_otp_auth"] is True
    sms_dependency = dependencies_response.json()["dependencies"]["sms_otp"]
    assert sms_dependency["status"] == "configured"
    assert sms_dependency["provider"] == "onhand"


def test_onhandsms_payload_template(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "ONHANDSMS_PAYLOAD_TEMPLATE",
        (
            '{"username":"{username}","password":"{password}","senderid":"{sender_id}",'
            '"number":"{phone_local}","istamil":"0","dlttemplateid":"{template_id}",'
            '"message":"{message}"}'
        ),
    )
    monkeypatch.setattr(main, "ONHANDSMS_USERNAME", "9944117857")
    monkeypatch.setattr(main, "ONHANDSMS_PASSWORD", "secret")
    monkeypatch.setattr(main, "ONHANDSMS_SENDER_ID", "SONAJS")
    monkeypatch.setattr(main, "ONHANDSMS_TEMPLATE_ID", "1707173372695978586")

    payload = main.render_onhandsms_payload("+919876543210", "Your OTP is 123456", "123456")

    assert payload == {
        "username": "9944117857",
        "password": "secret",
        "senderid": "SONAJS",
        "number": "9876543210",
        "istamil": "0",
        "dlttemplateid": "1707173372695978586",
        "message": "Your OTP is 123456",
    }


def test_onhandsms_payload_template_escapes_multiline_message(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "ONHANDSMS_PAYLOAD_TEMPLATE",
        (
            '{"username":"{username}","password":"{password}","senderid":"{sender_id}",'
            '"number":"{phone_local}","istamil":"0","dlttemplateid":"{template_id}",'
            '"message":"{message}"}'
        ),
    )
    monkeypatch.setattr(main, "ONHANDSMS_USERNAME", "9944117857")
    monkeypatch.setattr(main, "ONHANDSMS_PASSWORD", "secret")
    monkeypatch.setattr(main, "ONHANDSMS_SENDER_ID", "SONAJS")
    monkeypatch.setattr(main, "ONHANDSMS_TEMPLATE_ID", "1707173372695978586")

    message = (
        "Dear User,\n"
        "Your mobile verification code is 123456\n"
        "Please don't share this.\n"
        "Thanks,\n"
        "SONA JEWELLERS"
    )

    payload = main.render_onhandsms_payload("+919944117857", message, "123456")

    assert payload["number"] == "9944117857"
    assert payload["message"] == message


def test_onhandsms_send_handles_bad_payload_template(monkeypatch):
    import main

    monkeypatch.setattr(main, "ONHANDSMS_API_URL", "https://sms.example.test/send")
    monkeypatch.setattr(main, "ONHANDSMS_USERNAME", "9944117857")
    monkeypatch.setattr(main, "ONHANDSMS_PASSWORD", "secret")
    monkeypatch.setattr(main, "ONHANDSMS_SENDER_ID", "SONAJS")
    monkeypatch.setattr(main, "ONHANDSMS_PAYLOAD_TEMPLATE", "{bad-json")

    assert main.send_sms_via_onhand("+919944117857", "OTP 123456", "123456") is False


def test_onhandsms_query_request_uses_configured_user_agent(monkeypatch):
    import main

    captured = {}

    class DummyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"ok":true}'

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(main, "SMS_HTTP_USER_AGENT", "SonaTestClient/1.0")
    monkeypatch.setattr(main.urllib.request, "urlopen", fake_urlopen)

    status_code, body = main.query_http_request(
        "https://sms.example.test/send",
        {"number": "9944117857"},
        timeout=7,
    )

    assert status_code == 200
    assert body == {"ok": True}
    assert captured["url"].endswith("?number=9944117857")
    assert captured["timeout"] == 7
    assert captured["headers"]["User-agent"] == "SonaTestClient/1.0"


def test_otp_message_template_supports_hugging_face_escaped_newlines(monkeypatch):
    import main

    monkeypatch.setattr(
        main,
        "ONHANDSMS_MESSAGE_TEMPLATE",
        "Dear User,\\nYour mobile verification code is {otp}\\nPlease don't share this.",
    )

    assert main.render_otp_message("123456") == (
        "Dear User,\n"
        "Your mobile verification code is 123456\n"
        "Please don't share this."
    )
