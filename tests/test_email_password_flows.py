from datetime import timedelta

from models import User, EmailVerificationToken, PasswordResetToken, utc_now


def test_register_creates_unverified_user(client, db):
    response = client.post(
        "/register",
        json={
            "username": "verifyuser",
            "email": "verify@example.com",
            "password": "secret123"
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "verify@example.com"

    user = db.query(User).filter(User.email == "verify@example.com").first()
    assert user is not None
    assert user.is_verified is False

    token_row = db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == user.id
    ).first()
    assert token_row is not None
    assert token_row.is_used is False


def test_unverified_user_cannot_login(client):
    client.post(
        "/register",
        json={
            "username": "nologinuser",
            "email": "nologin@example.com",
            "password": "secret123"
        },
    )

    response = client.post(
        "/login",
        data={
            "username": "nologin@example.com",
            "password": "secret123"
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Please verify your email before logging in"


def test_verify_email_marks_user_verified(client, db):
    from main import generate_opaque_token, hash_opaque_token

    user = User(
        username="verifieduser",
        email="verified@example.com",
        hashed_password="hashed-password",
        is_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    raw_token = generate_opaque_token()
    token_row = EmailVerificationToken(
        user_id=user.id,
        token_hash=hash_opaque_token(raw_token),
        is_used=False,
        expires_at=utc_now() + timedelta(minutes=30)
    )
    db.add(token_row)
    db.commit()

    response = client.post("/verify-email", json={"token": raw_token})

    assert response.status_code == 200
    assert response.json()["message"] == "Email verified successfully"

    db.refresh(user)
    db.refresh(token_row)
    assert user.is_verified is True
    assert token_row.is_used is True


def test_verify_email_rejects_used_token(client, db):
    from main import generate_opaque_token, hash_opaque_token

    user = User(
        username="usedverify",
        email="usedverify@example.com",
        hashed_password="hashed-password",
        is_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    raw_token = generate_opaque_token()
    db.add(EmailVerificationToken(
        user_id=user.id,
        token_hash=hash_opaque_token(raw_token),
        is_used=True,
        expires_at=utc_now() + timedelta(minutes=30)
    ))
    db.commit()

    response = client.post("/verify-email", json={"token": raw_token})

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or expired verification token"


def test_verify_email_rejects_expired_token(client, db):
    from main import generate_opaque_token, hash_opaque_token

    user = User(
        username="expiredverify",
        email="expiredverify@example.com",
        hashed_password="hashed-password",
        is_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    raw_token = generate_opaque_token()
    db.add(EmailVerificationToken(
        user_id=user.id,
        token_hash=hash_opaque_token(raw_token),
        is_used=False,
        expires_at=utc_now() - timedelta(minutes=1)
    ))
    db.commit()

    response = client.post("/verify-email", json={"token": raw_token})

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or expired verification token"


def test_resend_verification_returns_generic_response_for_unknown_email(client):
    response = client.post(
        "/resend-verification",
        json={"email": "unknown@example.com"},
    )

    assert response.status_code == 200
    assert "If the email exists" in response.json()["message"]


def test_resend_verification_returns_generic_response_for_unverified_user(client, db):
    user = User(
        username="resenduser",
        email="resend@example.com",
        hashed_password="hashed-password",
        is_verified=False,
    )
    db.add(user)
    db.commit()

    response = client.post(
        "/resend-verification",
        json={"email": "resend@example.com"},
    )

    assert response.status_code == 200
    assert "If the email exists" in response.json()["message"]

    token_rows = db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == user.id
    ).all()
    assert len(token_rows) >= 1


def test_forgot_password_returns_generic_response_for_existing_email(client, db):
    user = User(
        username="resetuser",
        email="reset@example.com",
        hashed_password="hashed-password",
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    response = client.post("/forgot-password", json={"email": "reset@example.com"})

    assert response.status_code == 200
    assert "If that email is registered" in response.json()["message"]

    token_row = db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id
    ).first()
    assert token_row is not None
    assert token_row.is_used is False


def test_forgot_password_returns_generic_response_for_unknown_email(client):
    response = client.post("/forgot-password", json={"email": "unknown@example.com"})

    assert response.status_code == 200
    assert "If that email is registered" in response.json()["message"]


def test_reset_password_updates_password(client, db):
    from main import (
        hash_password,
        verify_password,
        generate_opaque_token,
        hash_opaque_token,
    )

    user = User(
        username="pwuser",
        email="pw@example.com",
        hashed_password=hash_password("oldpass123"),
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    raw_token = generate_opaque_token()
    token_row = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_opaque_token(raw_token),
        is_used=False,
        expires_at=utc_now() + timedelta(minutes=15)
    )
    db.add(token_row)
    db.commit()

    response = client.post(
        "/reset-password",
        json={
            "token": raw_token,
            "new_password": "newpass123"
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Password reset successful"

    db.refresh(user)
    db.refresh(token_row)
    assert verify_password("newpass123", user.hashed_password) is True
    assert token_row.is_used is True


def test_reset_password_rejects_expired_token(client, db):
    from main import hash_password, generate_opaque_token, hash_opaque_token

    user = User(
        username="expireduser",
        email="expired@example.com",
        hashed_password=hash_password("oldpass123"),
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    raw_token = generate_opaque_token()
    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=hash_opaque_token(raw_token),
        is_used=False,
        expires_at=utc_now() - timedelta(minutes=1)
    ))
    db.commit()

    response = client.post(
        "/reset-password",
        json={"token": raw_token, "new_password": "newpass123"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or expired reset token"


def test_reset_password_rejects_used_token(client, db):
    from main import hash_password, generate_opaque_token, hash_opaque_token

    user = User(
        username="useduser",
        email="used@example.com",
        hashed_password=hash_password("oldpass123"),
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    raw_token = generate_opaque_token()
    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=hash_opaque_token(raw_token),
        is_used=True,
        expires_at=utc_now() + timedelta(minutes=15)
    ))
    db.commit()

    response = client.post(
        "/reset-password",
        json={"token": raw_token, "new_password": "newpass123"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or expired reset token"


def test_login_works_after_email_verification(client, db):
    from main import hash_password, generate_opaque_token, hash_opaque_token

    user = User(
        username="loginverified",
        email="loginverified@example.com",
        hashed_password=hash_password("secret123"),
        is_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    raw_token = generate_opaque_token()
    db.add(EmailVerificationToken(
        user_id=user.id,
        token_hash=hash_opaque_token(raw_token),
        is_used=False,
        expires_at=utc_now() + timedelta(minutes=30)
    ))
    db.commit()

    verify_response = client.post("/verify-email", json={"token": raw_token})
    assert verify_response.status_code == 200

    login_response = client.post(
        "/login",
        data={
            "username": "loginverified@example.com",
            "password": "secret123"
        },
    )

    assert login_response.status_code == 200
    assert "access_token" in login_response.json()
