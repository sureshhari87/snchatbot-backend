import importlib.util
from pathlib import Path


def migration_modules():
    versions_dir = Path("alembic/versions")
    for migration_file in versions_dir.glob("*.py"):
        spec = importlib.util.spec_from_file_location(migration_file.stem, migration_file)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        yield migration_file, module


def test_alembic_revision_ids_fit_default_version_column():
    for migration_file, module in migration_modules():
        assert len(module.revision) <= 32, f"{migration_file} revision id is too long"
        if module.down_revision:
            assert len(module.down_revision) <= 32, f"{migration_file} down_revision is too long"

