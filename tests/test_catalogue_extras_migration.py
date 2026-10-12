"""Apply only the new migration to an isolated SQLite predecessor fixture."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration():
    path = Path("alembic/versions/0030_catalogue_extras.py")
    spec = importlib.util.spec_from_file_location("catalogue_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_preserves_predecessor_and_immutable_audit():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE products (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO users VALUES (3)")
        conn.exec_driver_sql("INSERT INTO products VALUES (24)")
        module = migration()
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        assert conn.exec_driver_sql("SELECT id FROM users").scalar_one() == 3
        assert conn.exec_driver_sql("SELECT id FROM products").scalar_one() == 24
        conn.exec_driver_sql("""INSERT INTO catalogue_content_audit
            (id,actor_id,resource,resource_id,version,reason,before,after,created_at)
            VALUES (1,3,'campaign',1,1,'Test audit',NULL,'{}',CURRENT_TIMESTAMP)""")
        for statement in [
            "UPDATE catalogue_content_audit SET reason='changed'",
            "DELETE FROM catalogue_content_audit",
        ]:
            with pytest.raises(sa.exc.IntegrityError):
                conn.exec_driver_sql(statement)
        with pytest.raises(RuntimeError, match="contain data"):
            module.downgrade()
    engine.dispose()


def test_empty_catalogue_extras_downgrade_leaves_predecessor():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE products (id INTEGER PRIMARY KEY)")
        module = migration()
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        module.downgrade()
        assert set(sa.inspect(conn).get_table_names()) == {"users", "products"}
    engine.dispose()
