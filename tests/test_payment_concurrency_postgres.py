"""Opt-in real row-lock tests on a disposable, loopback-only PostgreSQL cluster.

Never consumes DATABASE_URL or contacts Neon. Contains synthetic records only.
Set RUN_LOCAL_POSTGRES_TESTS=1 and install local PostgreSQL tools to run.
"""

import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from database import Base
from models import CustomerNotification, OrderSnapshot, Product, User
from schemas import OrderSyncRequest


@pytest.fixture(scope="module")
def local_postgres(tmp_path_factory):
    if os.environ.get("RUN_LOCAL_POSTGRES_TESTS") != "1":
        pytest.skip("Opt-in local PostgreSQL test")
    binaries = Path(__file__).resolve().parents[2] / "postgres-tools" / "pgsql" / "bin"
    if not (binaries / "initdb.exe").is_file():
        pytest.fail("Local PostgreSQL tools missing")
    tmp_path = tmp_path_factory.mktemp("payment-postgres")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    data = tmp_path / "postgres"
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}

    def run(tool, *args):
        # Windows server children can inherit PIPE handles and keep communicate()
        # waiting after pg_ctl exits. File-backed logs avoid that launcher hang.
        with (tmp_path / f"{tool}.log").open("ab") as log:
            subprocess.run(
                [str(binaries / (tool + ".exe")), *map(str, args)],
                check=True, stdout=log, stderr=log,
                timeout=180 if tool == "initdb" else 60, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

    # Durability is unnecessary for this disposable synthetic test database.
    run("initdb", "-D", data, "-U", "postgres", "-A", "trust", "--no-locale", "-E", "UTF8",
        "--no-sync")
    engine = None
    try:
        run("pg_ctl", "-D", data, "-l", tmp_path / "server.log", "-w", "start",
            "-o", f"-h 127.0.0.1 -p {port} -F")
        engine = create_engine(f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres")
        Base.metadata.create_all(engine)
        yield sessionmaker(bind=engine, autoflush=False)
    finally:
        if engine is not None:
            engine.dispose()
        if (data / "postmaster.pid").exists():
            run("pg_ctl", "-D", data, "-m", "fast", "-w", "stop")


@pytest.mark.parametrize("same_order", [True, False])
def test_parallel_verify_and_webhook_inventory_once(local_postgres, monkeypatch, same_order):
    factory = local_postgres
    with factory() as db:
        main.seed_products(db)
        user = User(username=f"parallel_{same_order}",
                    email=f"parallel_{same_order}@example.test", hashed_password="unused")
        db.add(user)
        db.commit()
        user_id = user.id
        orders = []
        for index in range(1 if same_order else 2):
            order = main.upsert_local_order_snapshot(db, user, OrderSyncRequest(
                order_reference=f"order_parallel_{same_order}_{index}", status="payment_pending",
                total=200, currency="INR", payment_status="pending", source="razorpay_checkout",
                items=[{"product_id": "snchatbot_1", "backend_product_id": 1,
                        "name": "Synthetic product", "qty": 2, "price": 100}],
            ))
            db.commit()
            orders.append(order.id)
        initial = db.get(Product, 1).stock_quantity

    def payment(payment_id, fallback=None):
        return 200, {"id": payment_id, "status": "captured", "amount": 20000, "currency": "INR"}

    monkeypatch.setattr(main, "fetch_razorpay_payment", payment)
    barrier = Barrier(2)

    def worker(index):
        with factory() as db:
            order = db.get(OrderSnapshot, orders[0 if same_order else index])
            payment_id = f"pay_parallel_{same_order}_{0 if same_order else index}"
            barrier.wait(timeout=10)
            if index == 0:
                result = main.finalize_razorpay_payment(db, order, payment_id, "verify")
            else:
                result = main.apply_razorpay_webhook(db, {
                    "id": f"evt_parallel_{same_order}", "event": "payment.captured",
                    "payload": {"payment": {"entity": {
                        "id": payment_id, "order_id": order.order_reference,
                        "status": "captured", "amount": 20000, "currency": "INR",
                    }}},
                })
            db.commit()
            return result["status"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, range(2)))
    assert sorted(results) == (["finalized", "idempotent"] if same_order else ["finalized"] * 2)
    with factory() as db:
        expected = 1 if same_order else 2
        assert db.get(Product, 1).stock_quantity == initial - 2 * expected
        assert db.query(CustomerNotification).filter_by(user_id=user_id).count() == expected
        assert all(order.payment_status == "verified"
                   for order in db.query(OrderSnapshot).filter_by(user_id=user_id))
