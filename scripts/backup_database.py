import argparse
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def safe_label(label: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in label)
    return cleaned.strip("-") or "manual"


def sqlite_path_from_url(database_url: str) -> Path:
    if database_url == "sqlite://":
        raise ValueError("In-memory SQLite databases cannot be backed up after the process exits.")
    if database_url.startswith("sqlite:///"):
        return Path(database_url.removeprefix("sqlite:///")).resolve()
    if database_url.startswith("sqlite://"):
        return Path(database_url.removeprefix("sqlite://")).resolve()
    raise ValueError("Unsupported SQLite DATABASE_URL format.")


def write_manifest(
    output_dir: Path,
    backup_file: Path,
    database_kind: str,
    label: str,
    status: str,
    extra: dict[str, str] | None = None,
) -> Path:
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database_kind": database_kind,
        "label": label,
        "status": status,
        "backup_file": str(backup_file),
        "backup_size_bytes": backup_file.stat().st_size if backup_file.exists() else 0,
    }
    if extra:
        manifest.update(extra)

    manifest_path = output_dir / f"{backup_file.stem}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest_path


def backup_sqlite(database_url: str, output_dir: Path, label: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_path = sqlite_path_from_url(database_url)
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite database does not exist: {source_path}")

    backup_file = output_dir / f"snchatbot-{safe_label(label)}-{utc_timestamp()}.sqlite3"
    source = sqlite3.connect(source_path)
    try:
        destination = sqlite3.connect(backup_file)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    manifest_path = write_manifest(
        output_dir,
        backup_file,
        "sqlite",
        label,
        "success",
        {"source_file": str(source_path)},
    )
    return backup_file, manifest_path


def pg_dump_executable(explicit_path: str | None = None) -> str:
    if explicit_path:
        return explicit_path
    resolved = shutil.which("pg_dump")
    if not resolved:
        raise FileNotFoundError(
            "pg_dump was not found. Install PostgreSQL client tools or set PG_DUMP_PATH."
        )
    return resolved


def backup_postgres(
    database_url: str,
    output_dir: Path,
    label: str,
    dump_path: str | None = None,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    backup_file = output_dir / f"snchatbot-{safe_label(label)}-{utc_timestamp()}.dump"
    command = [
        pg_dump_executable(dump_path),
        "--format=custom",
        "--no-owner",
        "--file",
        str(backup_file),
        database_url,
    ]
    subprocess.run(command, check=True)
    if not backup_file.exists() or backup_file.stat().st_size == 0:
        raise RuntimeError("pg_dump completed but produced an empty backup file.")

    manifest_path = write_manifest(
        output_dir,
        backup_file,
        "postgres",
        label,
        "success",
        {"restore_command": f"pg_restore --clean --if-exists --dbname <target-db-url> {backup_file}"},
    )
    return backup_file, manifest_path


def database_kind(database_url: str) -> str:
    parsed = urlparse(database_url)
    scheme = parsed.scheme.lower()
    if scheme.startswith("sqlite"):
        return "sqlite"
    if scheme in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        return "postgres"
    raise ValueError(f"Unsupported database scheme: {scheme}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a database backup/export before release.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output-dir", default="backups")
    parser.add_argument("--label", default="manual")
    parser.add_argument("--pg-dump-path", default=os.getenv("PG_DUMP_PATH"))
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit("DATABASE_URL is required or pass --database-url.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    kind = database_kind(args.database_url)
    if kind == "sqlite":
        backup_file, manifest_path = backup_sqlite(args.database_url, output_dir, args.label)
    else:
        backup_file, manifest_path = backup_postgres(
            args.database_url,
            output_dir,
            args.label,
            args.pg_dump_path,
        )

    print(f"Backup created: {backup_file}")
    print(f"Manifest created: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
