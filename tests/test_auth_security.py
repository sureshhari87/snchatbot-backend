from datetime import timedelta

import pytest

from main import (
    LOGIN_FAILURE_LIMIT,
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
)
from models import EmailVerificationToken, PasswordResetToken, RefreshToken, User, utc_now


def test_login_stores_refresh_token_audit_data(client, db, token_pair):
    token_row = (
        db.query(RefreshToken).filter(RefreshToken.token == token_pair["refresh_token"]).first()
    )

    assert token_row is not None
    assert token_row.token_jti
    assert token_row.family_id
    assert token_row.created_at is not None
    assert token_row.created_ip
    assert token_row.created_user_agent


def test_refresh_rotation_reuse_revokes_active_tokens(client, db, token_pair):
    old_refresh_token = token_pair["refresh_token"]

    rotate_response = client.post(
        "/refresh",
        json={"refresh_token": old_refresh_token},
    )

    assert rotate_response.status_code == 200
    new_refresh_token = rotate_response.json()["refresh_token"]
    assert new_refresh_token != old_refresh_token

    db.expire_all()
    old_token_row = db.query(RefreshToken).filter(RefreshToken.token == old_refresh_token).first()
    assert old_token_row.is_revoked is True
    assert old_token_row.revoked_reason == "rotated"
    assert old_token_row.replaced_by_token_id is not None

    reuse_response = client.post(
        "/refresh",
        json={"refresh_token": old_refresh_token},
    )

    assert reuse_response.status_code == 401

    active_token_response = client.post(
        "/refresh",
        json={"refresh_token": new_refresh_token},
    )

    assert active_token_response.status_code == 401

    db.expire_all()
    new_token_row = db.query(RefreshToken).filter(RefreshToken.token == new_refresh_token).first()
    active_count = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == old_token_row.user_id,
            RefreshToken.is_revoked == False,
        )
        .count()
    )
    assert new_token_row.is_revoked is True
    assert new_token_row.revoked_reason == "suspected_reuse"
    assert active_count == 0


def test_logout_revokes_refresh_token(client, db, token_pair):
    response = client.post(
        "/logout",
        headers={"Authorization": f"Bearer {token_pair['access_token']}"},
        json={"refresh_token": token_pair["refresh_token"]},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Logged out successfully"

    refresh_response = client.post(
        "/refresh",
        json={"refresh_token": token_pair["refresh_token"]},
    )
    assert refresh_response.status_code == 401

    db.expire_all()
    token_row = (
        db.query(RefreshToken).filter(RefreshToken.token == token_pair["refresh_token"]).first()
    )
    assert token_row.is_revoked is True
    assert token_row.revoked_reason == "logout"


def test_logout_all_devices_revokes_every_refresh_token(client, db, token_pair, verified_user):
    second_login = client.post(
        "/login",
        data={
            "username": verified_user.email,
            "password": "testpass123",
        },
    )
    assert second_login.status_code == 200
    second_refresh_token = second_login.json()["refresh_token"]

    response = client.post(
        "/logout-all-devices",
        headers={"Authorization": f"Bearer {token_pair['access_token']}"},
    )

    assert response.status_code == 200

    db.expire_all()
    refresh_tokens = db.query(RefreshToken).filter(RefreshToken.user_id == verified_user.id).all()
    assert len(refresh_tokens) == 2
    assert all(token.is_revoked for token in refresh_tokens)
    assert {token.revoked_reason for token in refresh_tokens} == {"logout_all_devices"}

    first_refresh_response = client.post(
        "/refresh",
        json={"refresh_token": token_pair["refresh_token"]},
    )
    second_refresh_response = client.post(
        "/refresh",
        json={"refresh_token": second_refresh_token},
    )

    assert first_refresh_response.status_code == 401
    assert second_refresh_response.status_code == 401


@pytest.mark.parametrize("password", ["short1", "password", "12345678"])
def test_register_rejects_weak_passwords(client, password):
    response = client.post(
        "/register",
        json={
            "username": f"weak_{password}",
            "email": f"weak_{password}@example.com",
            "password": password,
        },
    )

    assert response.status_code == 422
    assert "Password must be" in response.json()["detail"]


def test_reset_password_rejects_weak_new_password(client, db):
    user = User(
        username="weakreset",
        email="weakreset@example.com",
        hashed_password=hash_password("oldpass123"),
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    raw_token = generate_opaque_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_opaque_token(raw_token),
            is_used=False,
            expires_at=utc_now() + timedelta(minutes=15),
        )
    )
    db.commit()

    response = client.post(
        "/reset-password",
        json={
            "token": raw_token,
            "new_password": "password",
        },
    )

    assert response.status_code == 422
    assert "Password must be" in response.json()["detail"]


def test_resend_verification_cooldown_prevents_duplicate_token(client, db):
    user = User(
        username="cooldownuser",
        email="cooldown@example.com",
        hashed_password="hashed-password",
        is_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    first_response = client.post(
        "/resend-verification",
        json={"email": user.email},
    )
    second_response = client.post(
        "/resend-verification",
        json={"email": user.email},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    token_count = (
        db.query(EmailVerificationToken).filter(EmailVerificationToken.user_id == user.id).count()
    )
    assert token_count == 1


def test_login_lockout_after_repeated_failures(client, verified_user):
    for _ in range(LOGIN_FAILURE_LIMIT):
        response = client.post(
            "/login",
            data={
                "username": verified_user.email,
                "password": "wrongpass123",
            },
        )
        assert response.status_code == 401

    locked_response = client.post(
        "/login",
        data={
            "username": verified_user.email,
            "password": "testpass123",
        },
    )

    assert locked_response.status_code == 429
    assert "Too many failed login attempts" in locked_response.json()["detail"]
