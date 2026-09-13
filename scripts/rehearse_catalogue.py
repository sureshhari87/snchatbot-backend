"""Offline rehearsal only: source snapshot -> disposable in-memory SQLite."""

import json
import os
import sys
from pathlib import Path

os.environ["APP_ENV"] = "test"
os.environ["TESTING"] = "1"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["RUN_MIGRATIONS_ON_STARTUP"] = "0"
os.environ.setdefault("SECRET_KEY", "offline-catalogue-rehearsal-not-a-live-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from catalogue_data import import_snapshot
from database import Base
from models import Product


def main():
    snapshot = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        with db.begin():
            first = import_snapshot(db, snapshot)
        # Simulate one sale, then prove a repeated import cannot replenish stock.
        product = db.query(Product).filter(Product.stock_quantity > 0).first()
        if product:
            product.stock_quantity -= 1
            expected = product.stock_quantity
            db.commit()
        again = import_snapshot(db, snapshot)
        db.commit()
        if product and product.stock_quantity != expected:
            raise RuntimeError("Repeated import changed stock")
        print(
            json.dumps(
                {
                    "first_import": {k: v for k, v in first.items() if k != "mapping"},
                    "repeat_import": {k: v for k, v in again.items() if k != "mapping"},
                    "positive_prices": all(p.price > 0 for p in db.query(Product)),
                    "stock_preserved_on_repeat": True,
                    "production_modified": False,
                },
                indent=2,
            )
        )
    engine.dispose()


if __name__ == "__main__":
    main()
