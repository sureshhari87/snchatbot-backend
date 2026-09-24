import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from models import ProductReview, ReviewHelpfulVote


def test_additive_review_migration_round_trip(test_engine, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0015_product_reviews.py"
    spec = importlib.util.spec_from_file_location("review_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # Only an isolated in-memory SQLite fixture is used here.
    with test_engine.begin() as connection:
        ReviewHelpfulVote.__table__.drop(connection)
        ProductReview.__table__.drop(connection)
        existing_tables = set(inspect(connection).get_table_names())
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        assert set(inspect(connection).get_table_names()) == existing_tables | {
            "product_reviews",
            "review_helpful_votes",
        }
        constraints = inspect(connection).get_unique_constraints("product_reviews")
        assert any(c["name"] == "uq_review_user_product" for c in constraints)
        assert connection.execute(text("SELECT count(*) FROM product_reviews")).scalar() == 0
        migration.downgrade()
        assert set(inspect(connection).get_table_names()) == existing_tables
        migration.upgrade()
