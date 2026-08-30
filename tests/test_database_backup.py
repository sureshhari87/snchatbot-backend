import sqlite3
import tempfile
from pathlib import Path

from scripts import backup_database


def test_sqlite_backup_creates_database_copy_and_manifest():
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(
        prefix="database-backup-test-",
        dir=repo_root,
    ) as temp_dir:
        workspace = Path(temp_dir)
        source = workspace / "source.db"
        backup_dir = workspace / "backups"

        run_sqlite_backup_assertions(source, backup_dir)


def run_sqlite_backup_assertions(source: Path, backup_dir: Path):
    connection = sqlite3.connect(source)
    try:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        connection.execute("INSERT INTO users (email) VALUES ('customer@example.com')")
        connection.commit()
    finally:
        connection.close()

    backup_file, manifest_path = backup_database.backup_sqlite(
        f"sqlite:///{source}",
        backup_dir,
        "pre customer launch",
    )

    assert backup_file.exists()
    assert backup_file.stat().st_size > 0
    assert manifest_path.exists()
    assert "pre customer launch" in manifest_path.read_text()

    restored = sqlite3.connect(backup_file)
    try:
        row = restored.execute("SELECT email FROM users").fetchone()
    finally:
        restored.close()

    assert row == ("customer@example.com",)


def test_database_kind_detects_supported_urls():
    assert backup_database.database_kind("sqlite:///./jewellery.db") == "sqlite"
    assert backup_database.database_kind("postgresql://user:pass@host/db") == "postgres"
    assert backup_database.database_kind("postgresql+psycopg://user:pass@host/db") == "postgres"
