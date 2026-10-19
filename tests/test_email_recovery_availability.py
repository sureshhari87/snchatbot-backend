import pytest

import main
from models import EmailVerificationToken, PasswordResetToken, User


@pytest.mark.parametrize("path", ["/forgot-password", "/resend-verification"])
def test_unconfigured_mail_fails_equally_without_creating_tokens(client, db, monkeypatch, path):
    db.add(
        User(
            username="mailtest",
            email="known@example.com",
            hashed_password="unused",
            is_verified=False,
        )
    )
    db.commit()
    monkeypatch.setattr(main, "is_testing", lambda: False)
    monkeypatch.setattr(main, "email_provider_is_configured", lambda: False)
    responses = [
        client.post(path, json={"email": email})
        for email in ["known@example.com", "unknown@example.com"]
    ]
    assert [response.status_code for response in responses] == [503, 503]
    assert responses[0].json()["detail"] == responses[1].json()["detail"]
    assert responses[0].json()["detail"] == "Email recovery is temporarily unavailable"
    assert db.query(PasswordResetToken).count() == 0
    assert db.query(EmailVerificationToken).count() == 0


def test_skipped_email_does_not_log_address_or_reset_link(monkeypatch, capsys):
    events = []
    monkeypatch.setattr(main, "is_testing", lambda: True)
    monkeypatch.setattr(main, "log_event", lambda *args, **kwargs: events.append((args, kwargs)))
    assert (
        main.send_email("private@example.com", "Reset", "https://example.com/?token=private-token")
        is False
    )
    output = capsys.readouterr()
    assert "private" not in output.out + output.err + repr(events)
    assert events[0][0] == ("email.skipped",)
