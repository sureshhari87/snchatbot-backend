from unittest.mock import MagicMock, patch

import pytest

from scripts.local_staging import DATABASE, REVISION, ROLE, preflight

URL = "postgresql://staging_owner:test-only@ep-test.neon.tech/snchatbot_staging?sslmode=require"


@pytest.mark.parametrize("revision", ["0019_push_outbox", "0018_push_devices", "0017_system_notifications", "unexpected"])
def test_staging_preflight_requires_push_revision_read_only(revision):
    assert REVISION == "0019_push_outbox"
    identity = MagicMock()
    identity.fetchone.return_value = (DATABASE, ROLE)
    tables = [(name,) for name in (
        "alembic_version", "products", "push_devices", "push_events", "push_attempts"
    )]
    version = MagicMock()
    version.fetchall.return_value = [(revision,)]
    with patch("psycopg.connect") as connect:
        conn = connect.return_value.__enter__.return_value
        conn.execute.side_effect = [None, identity, tables, version]
        if revision == REVISION:
            assert preflight(URL) == "ready"
        else:
            with pytest.raises(ValueError, match="Unexpected schema revision"):
                preflight(URL)
        assert [call.args[0] for call in conn.execute.call_args_list] == [
            "SET TRANSACTION READ ONLY",
            "SELECT current_database(), current_user",
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'",
            "SELECT version_num FROM alembic_version",
        ]


@pytest.mark.parametrize("missing", ["push_devices", "push_events", "push_attempts"])
def test_expected_revision_without_required_table_is_rejected(missing):
    identity = MagicMock()
    identity.fetchone.return_value = (DATABASE, ROLE)
    version = MagicMock()
    version.fetchall.return_value = [(REVISION,)]
    tables = [(name,) for name in (
        "alembic_version", "products", "push_devices", "push_events", "push_attempts"
    ) if name != missing]
    with patch("psycopg.connect") as connect:
        conn = connect.return_value.__enter__.return_value
        conn.execute.side_effect = [None, identity, tables, version]
        with pytest.raises(ValueError, match="tables are missing"):
            preflight(URL)
        assert conn.execute.call_args_list[0].args[0] == "SET TRANSACTION READ ONLY"
