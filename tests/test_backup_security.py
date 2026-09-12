from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import backup_database


def test_dump_executable_must_exist_and_be_pg_dump(tmp_path):
    bad = tmp_path / "other.exe"
    bad.touch()
    with pytest.raises(ValueError):
        backup_database.pg_dump_executable(str(bad))
    good = tmp_path / "pg_dump.exe"
    good.touch()
    assert backup_database.pg_dump_executable(str(good)) == str(good.resolve())


def test_postgres_backup_keeps_password_out_of_arguments_and_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(
        backup_database, "pg_dump_executable", lambda _: str(tmp_path / "pg_dump.exe")
    )

    def run(command, **options):
        assert "private-password" not in " ".join(command)
        assert options["shell"] is False
        assert options["env"]["PGPASSWORD"] == "private-password"
        assert options["env"]["PGDATABASE"] == "testdb"
        Path(command[command.index("--file") + 1]).write_bytes(b"test-archive")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(backup_database.subprocess, "run", run)
    _, manifest = backup_database.backup_postgres(
        "postgresql+psycopg://user:private-password@localhost/testdb?sslmode=require",
        tmp_path,
        "test",
    )
    assert "private-password" not in manifest.read_text()
