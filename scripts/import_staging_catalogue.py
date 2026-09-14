"""Interactive staging-only catalogue import; credentials never written to disk."""

import getpass
import hashlib
import json
import os
import subprocess  # nosec B404 - fixed child script, no shell
import sys
import tempfile
import warnings
from pathlib import Path

from local_staging import REPO, clean_environment, preflight, validate_url


def import_child(snapshot_path):
    url = validate_url(os.environ["DATABASE_URL"])
    if preflight(url) != "ready":
        raise ValueError("Staging schema is not ready")
    sys.path.insert(0, str(REPO))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from catalogue_data import decode_document, import_snapshot, product_values, read_rates
    from models import Product

    raw = snapshot_path.read_bytes()
    snapshot = json.loads(raw)
    engine = create_engine("postgresql+psycopg://" + url.split("://", 1)[1])
    try:
        with Session(engine) as db:
            with db.begin():
                before = {p.source_id for p in db.query(Product) if p.source_id}
                report = import_snapshot(db, snapshot)
                rates = read_rates(db)
                checked = 0
                for document in snapshot["collections"]["products"]:
                    data = decode_document(document)
                    if not data:
                        continue
                    source_id = document["name"].rsplit("/", 1)[1]
                    product = db.query(Product).filter_by(source_id=source_id).one()
                    if source_id in before:
                        continue  # Preserve subsequent admin edits and stock changes.
                    expected = product_values(data, rates)
                    for field in ("price", "stock_quantity", "in_stock", "image", "source_data"):
                        if getattr(product, field) != expected[field]:
                            raise ValueError("Imported product verification failed")
                    checked += 1
                repeat = import_snapshot(db, snapshot)
                if repeat["inserted"] or repeat["mapping"] != report["mapping"]:
                    raise ValueError("Repeat import identity verification failed")
                total = db.query(Product).count()
            print(
                json.dumps(
                    {
                        "staging_import": "committed",
                        "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
                        **{k: v for k, v in report.items() if k != "mapping"},
                        "new_products_verified": checked,
                        "total_products_including_existing": total,
                        "image_urls_and_source_fields_preserved": True,
                        "image_network_checks": "pending",
                        "production_modified": False,
                    },
                    indent=2,
                )
            )
    finally:
        engine.dispose()


def main():
    if len(sys.argv) != 2 or not sys.stdin.isatty():
        raise ValueError("Run interactively with the snapshot path only")
    snapshot = Path(sys.argv[1]).resolve(strict=True)
    print("Select Neon branch staging, database snchatbot_staging, role staging_owner.")
    print("Use the DIRECT, pooling-OFF URL. Existing products will not be overwritten.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        url = validate_url(getpass.getpass("Paste staging URL (hidden): "))
    if input("Type IMPORT_STAGING to import the saved catalogue: ").strip() != "IMPORT_STAGING":
        print("Cancelled. No import ran.")
        return
    with tempfile.TemporaryDirectory(prefix="staging-import-") as working:
        result = subprocess.run(  # nosec B603 - fixed local script, credential-free argv
            [sys.executable, str(Path(__file__).resolve()), "--child", str(snapshot)],
            env=clean_environment(url),
            cwd=working,
            timeout=180,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        print(result.stdout)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--child":
            import_child(Path(sys.argv[2]))
        else:
            main()
    except (Exception, KeyboardInterrupt) as exc:
        print("Import stopped (" + type(exc).__name__ + "). Do not share credentials.")
        print("If interrupted, verify staging before retrying; no production target is accepted.")
        sys.exit(1)
