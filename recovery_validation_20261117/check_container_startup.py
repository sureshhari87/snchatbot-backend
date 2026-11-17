"""Exercise normal startup in an isolated CI container with a SQLite fixture."""

# Configuration guards must run before importing the test database models.
# ruff: noqa: E402

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

assert sys.platform == "linux" and sys.version_info[:2] == (3, 11)
assert os.environ.get("TESTING") == "1" and os.environ.get("APP_ENV") == "test"
assert os.environ.get("SECRET_KEY") == "synthetic-test-pinned-review-only-not-a-real-secret"
assert os.environ.get("DATABASE_URL") == "sqlite:////tmp/normal-startup.sqlite"
for name in ("RUN_MIGRATIONS_ON_STARTUP", "RUN_MIGRATIONS_BEFORE_START", "ADMIN_BOOTSTRAP_ENABLED"):
    assert os.environ.get(name) == "0"
assert os.environ.get("RECOVERY_EXPORT_ENABLED") == "0"
assert os.environ.get("APP_MODULE") == "main:app"
assert not Path("/tmp/normal-startup.sqlite").exists()
assert not list(Path("/app").glob(".env*"))
sys.path.insert(0, "/app")

import models  # noqa: F401
from database import Base, engine

# Fixture schema only, confined to the container's new temporary SQLite file.
Base.metadata.create_all(bind=engine)
table_count = len(Base.metadata.tables)
engine.dispose()
logs = Path("/tmp/normal-startup.log").open("xb")
process = subprocess.Popen(
    ["/app/scripts/start.sh"],
    cwd="/app",
    stdout=logs,
    stderr=subprocess.STDOUT,
    env=dict(os.environ),
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(path, method="GET"):
    message = urllib.request.Request("http://127.0.0.1:7860" + path, method=method)
    try:
        with opener.open(message, timeout=2) as response:
            return response.status, response.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as error:
        return error.code, error.read(4096)


try:
    deadline = time.monotonic() + 60
    while True:
        if process.poll() is not None:
            raise RuntimeError("Normal container startup exited before health passed")
        try:
            status, data = request("/health")
            if status == 200 and json.loads(data).get("status") == "ok":
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Normal container startup did not become healthy")
        time.sleep(0.5)
    ready_status, ready = request("/ready")
    assert ready_status == 200 and json.loads(ready).get("status") == "ok"
    schema_status, schema = request("/openapi.json")
    assert schema_status == 200
    assert "/admin/recovery/export" not in json.loads(schema)["paths"]
    export_status, _ = request("/admin/recovery/export", "POST")
    assert export_status == 404
finally:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    logs.close()

print(
    json.dumps(
        {
            "normal_entrypoint": "main:app",
            "linux_python_311_confirmed": True,
            "health_status": 200,
            "readiness_status": ready_status,
            "recovery_export_status": export_status,
            "fixture_database": "new container-local temporary SQLite file",
            "fixture_table_count": table_count,
            "database_migrations_run": False,
            "production_credentials_used": False,
            "production_changes": False,
        },
        indent=2,
    )
)
