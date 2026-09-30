import hashlib
from datetime import timedelta

from models import RefreshToken, utc_now
from push_devices import PushDevice, register_device


def proof(row):
    return {"token_jti": row.token_jti,
            "proof": hashlib.sha256(("sona-logout-v1:" + row.token).encode()).hexdigest()}


def test_expired_proof_revokes_only_old_family(client, db, token_pair, verified_user, monkeypatch):
    monkeypatch.setenv("PUSH_DEVICE_REGISTRATION_ENABLED", "1")
    old = db.query(RefreshToken).filter_by(token=token_pair["refresh_token"]).one()
    register_device(db, verified_user.id, old.token, "synthetic-old-device-token")
    old.expires_at = utc_now() - timedelta(seconds=1)
    newer = RefreshToken(user_id=verified_user.id, token="synthetic-new-session",
                         token_jti="new-session-jti", family_id="new-family",
                         is_revoked=False, expires_at=utc_now() + timedelta(days=1))
    db.add(newer)
    db.commit()
    register_device(db, verified_user.id, newer.token, "synthetic-new-device-token")
    db.commit()
    for _ in range(2):
        response = client.post("/auth/sessions/revoke", json=proof(old))
        assert response.status_code == 200
        assert response.json() == {"revocation_processed": True}
    db.expire_all()
    assert old.is_revoked
    assert not newer.is_revoked
    assert db.query(PushDevice).one().family_id == "new-family"


def test_wrong_proof_and_unknown_id_do_not_revoke(client, db, token_pair, monkeypatch):
    monkeypatch.setenv("PUSH_DEVICE_REGISTRATION_ENABLED", "1")
    row = db.query(RefreshToken).filter_by(token=token_pair["refresh_token"]).one()
    data = proof(row)
    for invalid in ({**data, "proof": "0" * 64}, {**data, "token_jti": "unknown"}):
        assert client.post("/auth/sessions/revoke", json=invalid).json() == {"revocation_processed": True}
    db.refresh(row)
    assert not row.is_revoked
    assert client.post("/auth/sessions/revoke", json={**data, "user_id": 1}).status_code == 422
    monkeypatch.setenv("PUSH_DEVICE_REGISTRATION_ENABLED", "0")
    assert client.post("/auth/sessions/revoke", json=data).status_code == 503
