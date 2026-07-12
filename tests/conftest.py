import os

os.environ.setdefault("APP_ENV", "test")
os.environ["TESTING"] = "1"
os.environ.setdefault("RUN_MIGRATIONS_ON_STARTUP", "0")

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from main import (
    LOGIN_FAILURES,
    app,
    get_db,
    hash_password,
    reset_observability_metrics,
    seed_products,
)
from models import User


@pytest.fixture(scope="function")
def test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture(scope="function")
def db(test_engine):
    testing_session_local = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_engine,
    )
    db = testing_session_local()
    seed_products(db)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="function")
def client(db):
    LOGIN_FAILURES.clear()
    reset_observability_metrics()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    LOGIN_FAILURES.clear()
    reset_observability_metrics()


@pytest.fixture
def user_data():
    unique = uuid.uuid4().hex[:8]
    return {
        "username": f"user_{unique}",
        "email": f"user_{unique}@example.com",
        "password": "testpass123",
    }


@pytest.fixture
def registered_user(client, db, user_data):
    response = client.post("/register", json=user_data)
    assert response.status_code == 200
    user = db.query(User).filter(User.email == user_data["email"]).first()
    user.is_verified = True
    db.commit()
    return user_data


@pytest.fixture
def login_response(client, verified_user):
    response = client.post(
        "/login",
        data={"username": verified_user.email, "password": "testpass123"},
    )
    assert response.status_code == 200
    return response


@pytest.fixture
def token_pair(login_response):
    body = login_response.json()
    return {
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
    }


@pytest.fixture
def access_token(token_pair):
    return token_pair["access_token"]


@pytest.fixture
def refresh_token(token_pair):
    return token_pair["refresh_token"]


@pytest.fixture
def auth_headers(access_token):
    return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture
def admin_user(db):
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"admin_{unique}",
        email=f"admin_{unique}@example.com",
        hashed_password=hash_password("adminpass123"),
        is_verified=True,
        is_admin=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def admin_headers(client, admin_user):
    response = client.post(
        "/login",
        data={
            "username": admin_user.email,
            "password": "adminpass123",
        },
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def auth_client(client, auth_headers):
    class AuthClient:
        def post(self, url, **kwargs):
            headers = kwargs.pop("headers", {})
            headers.update(auth_headers)
            return client.post(url, headers=headers, **kwargs)

        def get(self, url, **kwargs):
            headers = kwargs.pop("headers", {})
            headers.update(auth_headers)
            return client.get(url, headers=headers, **kwargs)

    return AuthClient()


@pytest.fixture
def verified_user(db, user_data):
    user = User(
        username=user_data["username"],
        email=user_data["email"],
        hashed_password=hash_password(user_data["password"]),
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
