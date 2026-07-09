import os
os.environ["TESTING"] = "1"

import uuid
import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from database import Base
from main import app, get_db, seed_products
from models import User
from main import hash_password

SQLALCHEMY_TEST_DATABASE_URL = "sqlite:///./test_jewellery.db"

engine = create_engine(
    SQLALCHEMY_TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


@pytest.fixture(scope="function")
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    seed_products(db)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="function")
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


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
        data={
            "username": verified_user.email,
            "password": "testpass123"
        },
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
