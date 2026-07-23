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

    response = client.post(
        "/admin/email/test",
        headers=admin_headers,
        json={"to_email": "owner@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "SMTP test email sent"
    assert sent_messages == [
        (
            "owner@example.com",
            "Jewellery Chat SMTP test",
            (
                "This is a test email from your Jewellery Chat backend.\n\n"
                "If you received this, SMTP delivery is working."
            ),
        )
    ]


def test_admin_email_test_endpoint_reports_missing_smtp(client, admin_headers, monkeypatch):
    monkeypatch.setattr(main, "EMAIL_HOST", None)

    response = client.post("/admin/email/test", headers=admin_headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "SMTP is not configured"
