from datetime import timedelta

import pytest

from models import RefreshToken, utc_now
from push_devices import (
    PushDevice,
    RegistrationRejected,
    detach_all,
    detach_family,
    eligible_devices,
    register_device,
)


def session(db, user_id, family="family-one", token="test-refresh-one"):
    row = RefreshToken(
        user_id=user_id,
        token=token,
        family_id=family,
        is_revoked=False,
        expires_at=utc_now() + timedelta(days=1),
    )
    db.add(row)
    db.commit()
    return row


def test_registration_repeat_rotation_and_session_rotation(db, verified_user):
    first = session(db, verified_user.id)
    device_id = register_device(db, verified_user.id, first.token, "test-fcm-token-00000001")
    db.commit()
    assert (
        register_device(db, verified_user.id, first.token, "test-fcm-token-00000001") == device_id
    )
    first.is_revoked = True
    replacement = session(db, verified_user.id, token="test-refresh-two")
    assert (
        register_device(db, verified_user.id, replacement.token, "test-fcm-token-00000002")
        == device_id
    )
    db.commit()
    assert db.query(PushDevice).count() == 1
    assert eligible_devices(db, verified_user.id)[0].push_token == "test-fcm-token-00000002"
    replacement.is_revoked = True
    db.commit()
    assert eligible_devices(db, verified_user.id) == []


def test_invalid_session_and_token_rejected(db, verified_user, admin_user):
    row = session(db, verified_user.id)
    for token in ("", "short", "x" * 4097, "has whitespace" * 3):
        with pytest.raises(RegistrationRejected):
            register_device(db, verified_user.id, row.token, token)
    with pytest.raises(RegistrationRejected):
        register_device(db, admin_user.id, row.token, "test-fcm-token-00000001")
    row.expires_at = utc_now() - timedelta(seconds=1)
    db.commit()
    with pytest.raises(RegistrationRejected):
        register_device(db, verified_user.id, row.token, "test-fcm-token-00000001")
    assert db.query(PushDevice).count() == 0


def test_conflict_and_owner_scoped_detach(db, verified_user, admin_user):
    first = session(db, verified_user.id)
    other = session(db, admin_user.id, "family-two", "test-refresh-two")
    register_device(db, verified_user.id, first.token, "test-fcm-token-00000001")
    db.commit()
    with pytest.raises(RegistrationRejected, match="conflict"):
        register_device(db, admin_user.id, other.token, "test-fcm-token-00000001")
    db.commit()
    assert detach_family(db, admin_user.id, first.family_id) == 0
    assert detach_all(db, admin_user.id) == 0
    assert len(eligible_devices(db, verified_user.id)) == 1
    assert detach_family(db, verified_user.id, first.family_id) == 1
    db.commit()
    assert detach_family(db, verified_user.id, first.family_id) == 0
    register_device(db, admin_user.id, other.token, "test-fcm-token-00000001")
    db.commit()
    assert eligible_devices(db, verified_user.id) == []
    assert len(eligible_devices(db, admin_user.id)) == 1
