"""API contract used by the November customer and admin Flutter releases."""

import pytest

from main import app
from scripts.smoke_test import FLUTTER_RELEASE_ROUTES


@pytest.mark.parametrize(
    ("path", "methods"),
    FLUTTER_RELEASE_ROUTES.items(),
)
def test_flutter_release_routes_remain_authenticated(path, methods):
    paths = app.openapi()["paths"]
    assert path in paths, f"Flutter release requires {path}"
    for method in methods:
        assert method in paths[path], f"Flutter release requires {method.upper()} {path}"
        assert paths[path][method].get("security"), f"{method.upper()} {path} must require auth"
