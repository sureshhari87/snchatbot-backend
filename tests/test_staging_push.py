from types import SimpleNamespace

import pytest

import fcm_sender
from push_outbox import DeliveryUnavailableError
from staging_push import recipients, verify_database


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("PUSH_STAGING_ALLOWED_USER_IDS", "3,5")
    monkeypatch.setenv("STAGING_NEON_HOST", "ep-synthetic.neon.tech")


@pytest.mark.parametrize("raw", ["", "*", "0", "-1", "3,", "3, 5", "1,2,3,4,5,6", "2147483648"])
def test_invalid_allowlist_fails_closed(monkeypatch, raw):
    monkeypatch.setenv("PUSH_STAGING_ALLOWED_USER_IDS", raw)
    with pytest.raises(DeliveryUnavailableError):
        recipients()


def test_production_rejected_before_database(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(DeliveryUnavailableError):
        verify_database(lambda: pytest.fail("Opened production database"))


class Database:
    identity = ("snchatbot_staging", "staging_owner")
    versions = ["0019_push_outbox"]

    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"),
                               url=SimpleNamespace(host="ep-synthetic.neon.tech",
                                                   query={"sslmode": "require"}))

    def execute(self, sql):
        self.statements.append(str(sql))
        return self

    def one(self):
        return self.identity

    def scalars(self):
        return self

    def all(self):
        return self.versions


def test_identity_schema_check_is_read_only():
    db = Database()
    verify_database(lambda: db)
    assert db.statements[0] == "SET TRANSACTION READ ONLY"
    assert recipients() == {3, 5}


@pytest.mark.parametrize("identity,versions", [
    (("neondb", "neondb_owner"), ["0019_push_outbox"]),
    (("snchatbot_staging", "neondb_owner"), ["0019_push_outbox"]),
    (("snchatbot_staging", "staging_owner"), ["0018_push_devices"]),
])
def test_wrong_database_role_or_revision_rejected(identity, versions):
    db = Database()
    db.identity, db.versions = identity, versions
    with pytest.raises(DeliveryUnavailableError):
        verify_database(lambda: db)


def test_wrong_host_rejected(monkeypatch):
    monkeypatch.setenv("STAGING_NEON_HOST", "ep-other.neon.tech")
    with pytest.raises(DeliveryUnavailableError):
        verify_database(Database)


def test_database_guard_runs_before_credentials(monkeypatch):
    monkeypatch.setattr(fcm_sender, "delivery_enabled", lambda: True)
    def reject(factory):
        raise DeliveryUnavailableError("Blocked")
    monkeypatch.setattr(fcm_sender, "verify_database", reject)
    monkeypatch.setattr(fcm_sender, "prepare_sender", lambda: pytest.fail("Credentials loaded"))
    with pytest.raises(DeliveryUnavailableError):
        fcm_sender.run_configured_batch(None)


def test_tls_is_required():
    db = Database()
    bind = db.get_bind()
    bind.url.query = {}
    db.get_bind = lambda: bind
    with pytest.raises(DeliveryUnavailableError):
        verify_database(lambda: db)


def test_operator_default_and_cancel_never_load_sender(monkeypatch):
    from pathlib import Path

    import database
    import staging_push
    from scripts.staging_push_worker import main

    class Queue(Database):
        def query(self, *args):
            return self
        def join(self, *args):
            return self
        def filter(self, *args):
            return self
        def count(self):
            return 1

    monkeypatch.setattr(Path, "exists", lambda _: False)
    monkeypatch.setattr(database, "SessionLocal", Queue)
    monkeypatch.setattr(staging_push, "verify_database", lambda factory: None)
    monkeypatch.setattr(fcm_sender, "prepare_sender", lambda: pytest.fail("Sender loaded"))
    assert main([]) == 0
    monkeypatch.setattr("builtins.input", lambda _: "CANCEL")
    assert main(["--send"]) == 1


def test_batch_is_filtered_and_sender_closed(monkeypatch):
    import push_outbox

    calls = []
    monkeypatch.setattr(fcm_sender, "delivery_enabled", lambda: True)
    monkeypatch.setattr(fcm_sender, "verify_database", lambda factory: calls.append("verified"))
    def prepare():
        assert calls == ["verified"]
        return SimpleNamespace(close=lambda: calls.append("closed"))
    monkeypatch.setattr(fcm_sender, "prepare_sender", prepare)
    def batch(factory, sender, **kwargs):
        assert kwargs == {"enabled": True, "limit": 1, "allowed_user_ids": {3, 5}}
        return ["sent"]
    monkeypatch.setattr(push_outbox, "run_batch", batch)
    assert fcm_sender.run_configured_batch(None) == ["sent"]
    assert calls == ["verified", "closed"]
    with pytest.raises(ValueError):
        fcm_sender.run_configured_batch(None, limit=6)
