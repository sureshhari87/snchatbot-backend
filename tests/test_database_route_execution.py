"""Blocking catalogue and readiness work must not run on the event loop."""

import asyncio
import inspect
from unittest.mock import MagicMock

import pytest

import main


def test_only_nonblocking_main_routes_remain_async():
    allowed = {"root", "health", "receive_razorpay_webhook"}
    for route in main.app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint and endpoint.__module__ == "main" and inspect.iscoroutinefunction(endpoint):
            assert endpoint.__name__ in allowed


def test_concurrent_requests_release_small_pool(tmp_path, monkeypatch):
    from fastapi import Depends, FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import QueuePool

    from database import Base
    from models import User

    engine = create_engine(
        "sqlite:///" + str(tmp_path / "concurrency.db"),
        connect_args={"check_same_thread": False}, poolclass=QueuePool,
        pool_size=2, max_overflow=0, pool_timeout=1,
    )
    factory = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    with factory() as db:
        db.add(User(username="pool", email="pool@example.test", hashed_password="unused"))
        db.commit()

    def get_db():
        with factory() as db:
            yield db

    def user(db=Depends(main.get_db)):
        # Authentication checks out a connection before the route executes.
        return db.query(User).one()

    def snapshot(db):
        db.execute(text("SELECT 1"))
        return {"status": "ok", "dependencies": {}}

    app = FastAPI()
    app.include_router(main.app.router)
    app.dependency_overrides[main.get_db] = get_db
    app.dependency_overrides[main.get_current_user] = user
    monkeypatch.setattr(main, "dependency_snapshot", snapshot)

    async def exercise():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            paths = ["/wishlist", "/products", "/catalogue/metal-rates", "/ready"] * 8
            results = await asyncio.gather(*(client.get(path) for path in paths))
            assert all(r.status_code == 200 for r in results), [r.status_code for r in results]

    try:
        asyncio.run(exercise())
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()


def assert_worker_thread():
    with pytest.raises(RuntimeError, match="no running event loop"):
        asyncio.get_running_loop()


def test_login_failure_bookkeeping_under_worker_concurrency():
    from concurrent.futures import ThreadPoolExecutor

    from starlette.requests import Request

    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1234)})
    email = "parallel-lockout@example.test"
    main.clear_login_failures(email, request)

    def record(_):
        for _ in range(20):
            main.login_is_locked(email, request)
            main.record_login_failure(email, request)

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(record, range(8)))
        assert len(main.LOGIN_FAILURES[main.login_failure_key(email, request)]) == 160
        assert main.login_is_locked(email, request)
    finally:
        main.clear_login_failures(email, request)


@pytest.mark.parametrize("path", ["/ready", "/readiness", "/dependencies"])
@pytest.mark.parametrize("healthy", [True, False])
def test_readiness_runs_off_event_loop(client, monkeypatch, path, healthy):
    calls = []
    snapshot = {"status": "ok" if healthy else "error", "dependencies": {}}

    def probe(db):
        assert_worker_thread()
        calls.append(True)
        return snapshot

    monkeypatch.setattr(main, "dependency_snapshot", probe)
    response = client.get(path)
    assert response.status_code == (200 if healthy else 503)
    assert response.json() == snapshot
    assert calls == [True]


def test_product_query_runs_off_event_loop(client, monkeypatch):
    calls = []
    query = MagicMock()
    query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

    def search(db, **kwargs):
        assert_worker_thread()
        calls.append(kwargs)
        return query

    monkeypatch.setattr(main, "product_search_query", search)
    response = client.get("/products?limit=2&offset=1&metal=gold")
    assert response.status_code == 200
    assert response.json() == []
    assert calls[0]["metal"] == "gold"
    query.order_by.return_value.offset.assert_called_once_with(1)
    query.order_by.return_value.offset.return_value.limit.assert_called_once_with(2)
