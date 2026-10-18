"""Read-only review schema checks: no real database or migration."""

from unittest.mock import MagicMock

import pytest
from test_staging_feature_revisions import URL, database

from scripts import local_staging as local
from scripts import render_staging as render


@pytest.mark.parametrize("missing_column", [False, True])
def test_review_column_required_and_reads_only(monkeypatch, missing_column):
    conn = database(monkeypatch, local.REVIEW_PURCHASE_REVISION)
    previous = conn.execute.side_effect

    def execute(statement):
        if "information_schema.columns" in statement:
            result = MagicMock()
            result.fetchall.return_value = [] if missing_column else [("verified_order_id",)]
            return result
        return previous(statement)

    conn.execute.side_effect = execute
    if missing_column:
        with pytest.raises(ValueError, match="attribution column"):
            local.preflight(URL, expected_revision=local.REVIEW_PURCHASE_REVISION)
    else:
        assert local.preflight(URL, expected_revision=local.REVIEW_PURCHASE_REVISION) == "ready"
    assert all(
        call.args[0].startswith("SELECT ") or call.args[0] == "SET TRANSACTION READ ONLY"
        for call in conn.execute.call_args_list
    )


def test_render_rejects_previous_schema_even_with_features_off(monkeypatch):
    database(monkeypatch, local.REFERRAL_REVISION)
    monkeypatch.setenv("REFERRALS_ENABLED", "0")
    with pytest.raises(ValueError, match="Unexpected schema"):
        render.verify_database(URL)


@pytest.mark.parametrize("has_table", [False, True])
def test_photo_revision_needs_attribution_and_private_storage(monkeypatch, has_table):
    conn = database(monkeypatch, local.REVIEW_PHOTOS_REVISION)
    previous = conn.execute.side_effect

    def execute(statement):
        if "information_schema.columns" in statement:
            result = MagicMock()
            result.fetchall.return_value = [("verified_order_id",)]
            return result
        result = previous(statement)
        if has_table and "pg_tables" in statement:
            values = list(result)
            result.__iter__.return_value = iter(values + [("review_photos",)])
        return result

    conn.execute.side_effect = execute
    if has_table:
        assert local.preflight(URL, expected_revision=local.REVIEW_PHOTOS_REVISION) == "ready"
    else:
        with pytest.raises(ValueError, match="photo schema"):
            local.preflight(URL, expected_revision=local.REVIEW_PHOTOS_REVISION)


def test_photo_flag_defaults_off_and_rejects_ambiguous_values():
    from test_financial_staging_guard import settings

    assert render.render_environment(settings())["REVIEW_PHOTOS_ENABLED"] == "0"
    with pytest.raises(ValueError):
        render.render_environment({**settings(), "REVIEW_PHOTOS_ENABLED": "yes"})
