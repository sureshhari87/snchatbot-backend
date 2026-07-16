import main
from models import User
from scripts import create_admin_user


def test_bootstrap_admin_user_creates_verified_admin(db, monkeypatch):
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_EMAIL", "owner@example.com")
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_USERNAME", "owner_admin")
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_PASSWORD", "adminpass123")

    main.bootstrap_admin_user(db)

    user = db.query(User).filter(User.email == "owner@example.com").first()
    assert user is not None
    assert user.username == "owner_admin"
    assert user.is_admin is True
    assert user.is_verified is True
    assert main.verify_password("adminpass123", user.hashed_password)


def test_bootstrap_admin_user_promotes_existing_user(db, monkeypatch):
    user = User(
        username="support_owner",
        email="support@example.com",
        hashed_password=main.hash_password("customer123"),
        is_verified=False,
        is_admin=False,
    )
    db.add(user)
    db.commit()

    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_EMAIL", "support@example.com")
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_USERNAME", "support_owner")
    monkeypatch.setattr(main, "ADMIN_BOOTSTRAP_PASSWORD", "adminpass123")

    main.bootstrap_admin_user(db)
    db.refresh(user)

    assert user.is_admin is True
    assert user.is_verified is True
    assert main.verify_password("adminpass123", user.hashed_password)


def test_customer_token_cannot_access_admin_routes(client, auth_headers):
    response = client.get("/admin/metrics", headers=auth_headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"


def test_create_admin_user_script_helper_creates_admin(db, monkeypatch):
    monkeypatch.setattr(create_admin_user, "SessionLocal", lambda: db)

    message = create_admin_user.create_or_promote_admin(
        email="script-admin@example.com",
        username="script_admin",
        password="adminpass123",
        promote_existing=False,
    )

    user = db.query(User).filter(User.email == "script-admin@example.com").first()
    assert message == "Created admin user: script-admin@example.com"
    assert user is not None
    assert user.is_admin is True
    assert user.is_verified is True
