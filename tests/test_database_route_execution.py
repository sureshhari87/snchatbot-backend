"""Blocking catalogue and readiness work must not run on the event loop."""

import asyncio
from unittest.mock import MagicMock

import pytest

import main


def assert_worker_thread():
    with pytest.raises(RuntimeError, match="no running event loop"):
        asyncio.get_running_loop()


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
