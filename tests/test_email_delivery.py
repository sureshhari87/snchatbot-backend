import main


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args = None
        self.message = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.message = message


def test_send_email_uses_configured_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(main, "is_testing", lambda: False)
    monkeypatch.setattr(main, "EMAIL_PROVIDER", "smtp")
    monkeypatch.setattr(main, "EMAIL_HOST", "smtp.example.com")
    monkeypatch.setattr(main, "EMAIL_PORT", 587)
    monkeypatch.setattr(main, "EMAIL_USERNAME", "mailer@example.com")
    monkeypatch.setattr(main, "EMAIL_PASSWORD", "secret")
    monkeypatch.setattr(main, "EMAIL_FROM", "noreply@example.com")
    monkeypatch.setattr(main, "EMAIL_FROM_NAME", "Jewellery Chat")
    monkeypatch.setattr(main, "EMAIL_USE_TLS", True)
    monkeypatch.setattr(main, "EMAIL_USE_SSL", False)
    monkeypatch.setattr(main.smtplib, "SMTP", FakeSMTP)

    sent = main.send_email(
        "customer@example.com",
        "Welcome",
        "Hello from Jewellery Chat",
    )

    assert sent is True
    smtp = FakeSMTP.instances[0]
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 587
    assert smtp.started_tls is True
    assert smtp.login_args == ("mailer@example.com", "secret")
    assert smtp.message["To"] == "customer@example.com"
    assert smtp.message["Subject"] == "Welcome"


def test_send_email_uses_resend_https_provider(monkeypatch):
    request_calls = []

    def capture_request(method, url, payload, headers=None, timeout=10):
        request_calls.append(
            {
                "method": method,
                "url": url,
                "payload": payload,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return 200, {"id": "email_123"}

    monkeypatch.setattr(main, "is_testing", lambda: False)
    monkeypatch.setattr(main, "EMAIL_PROVIDER", "resend")
    monkeypatch.setattr(main, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(main, "RESEND_API_URL", "https://api.resend.com/emails")
    monkeypatch.setattr(main, "EMAIL_FROM", "noreply@example.com")
    monkeypatch.setattr(main, "EMAIL_FROM_NAME", "Jewellery Chat")
    monkeypatch.setattr(main, "EMAIL_TIMEOUT_SECONDS", 12)
    monkeypatch.setattr(main, "json_http_request", capture_request)

    sent = main.send_email(
        "customer@example.com",
        "Welcome",
        "Hello from Jewellery Chat",
    )

    assert sent is True
    assert request_calls == [
        {
            "method": "POST",
            "url": "https://api.resend.com/emails",
            "payload": {
                "from": "Jewellery Chat <noreply@example.com>",
                "to": ["customer@example.com"],
                "subject": "Welcome",
                "text": "Hello from Jewellery Chat",
            },
            "headers": {"Authorization": "Bearer re_test_key"},
            "timeout": 12,
        }
    ]


def test_send_email_uses_brevo_https_provider(monkeypatch):
    request_calls = []

    def capture_request(method, url, payload, headers=None, timeout=10):
        request_calls.append(
            {
                "method": method,
                "url": url,
                "payload": payload,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return 201, {"messageId": "<message-id@relay.domain.com>"}

    monkeypatch.setattr(main, "is_testing", lambda: False)
    monkeypatch.setattr(main, "EMAIL_PROVIDER", "brevo")
    monkeypatch.setattr(main, "BREVO_API_KEY", "xkeysib-test")
    monkeypatch.setattr(main, "BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")
    monkeypatch.setattr(main, "EMAIL_FROM", "noreply@example.com")
    monkeypatch.setattr(main, "EMAIL_FROM_NAME", "Jewellery Chat")
    monkeypatch.setattr(main, "EMAIL_TIMEOUT_SECONDS", 12)
    monkeypatch.setattr(main, "json_http_request", capture_request)

    sent = main.send_email(
        "customer@example.com",
        "Welcome",
        "Hello from Jewellery Chat",
    )

    assert sent is True
    assert request_calls == [
        {
            "method": "POST",
            "url": "https://api.brevo.com/v3/smtp/email",
            "payload": {
                "sender": {
                    "name": "Jewellery Chat",
                    "email": "noreply@example.com",
                },
                "to": [{"email": "customer@example.com"}],
                "subject": "Welcome",
                "textContent": "Hello from Jewellery Chat",
            },
            "headers": {"api-key": "xkeysib-test"},
            "timeout": 12,
        }
    ]


def test_send_email_skips_delivery_in_test_mode(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setattr(main, "EMAIL_HOST", "smtp.example.com")

    sent = main.send_email(
        "customer@example.com",
        "Welcome",
        "Hello from Jewellery Chat",
    )

    assert sent is False


def test_admin_email_test_endpoint_sends_test_email(client, admin_headers, monkeypatch):
    sent_messages = []

    def capture_email(to_email, subject, body):
        sent_messages.append((to_email, subject, body))
        return True

    monkeypatch.setattr(main, "EMAIL_HOST", "smtp.example.com")
    monkeypatch.setattr(main, "send_email", capture_email)
    monkeypatch.setattr(main, "email_provider_is_configured", lambda: True)

    response = client.post(
        "/admin/email/test",
        headers=admin_headers,
        json={"to_email": "owner@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Email test sent"
    assert sent_messages == [
        (
            "owner@example.com",
            "Jewellery Chat email test",
            (
                "This is a test email from your Jewellery Chat backend.\n\n"
                "If you received this, email delivery is working."
            ),
        )
    ]


def test_admin_email_test_endpoint_reports_missing_provider(client, admin_headers, monkeypatch):
    monkeypatch.setattr(main, "email_provider_is_configured", lambda: False)

    response = client.post("/admin/email/test", headers=admin_headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "Email provider is not configured"
