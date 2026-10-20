"""Advertised records must not disappear from paginated adverse evidence."""

import pytest
from fastapi import HTTPException

from financial_provider import capture_id, collection


class Provider:
    def __init__(self, pages):
        self.pages = pages
        self.calls = 0

    def call_razorpay(self, method, path):
        assert method == "GET" and path.startswith("disputes?")
        page = self.pages[self.calls]
        self.calls += 1
        return 200, page


def page(count, ids):
    return {"entity": "collection", "count": count, "items": [{"id": value} for value in ids]}


@pytest.mark.parametrize("count,ids", [(1, []), (2, ["disp_one"])])
def test_short_page_with_unretrieved_total_is_uncertain(count, ids):
    with pytest.raises(HTTPException) as error:
        collection(Provider([page(count, ids)]), "disputes")
    assert error.value.status_code == 503
    assert (
        error.value.detail
        == "Financial provider collection unavailable [disputes:incomplete_collection]"
    )


@pytest.mark.parametrize("totals", [(101, 101), (100, 1)])
def test_complete_total_or_page_counts_are_accepted(totals):
    ids = ["disp_" + str(i) for i in range(101)]
    result = collection(
        Provider([page(totals[0], ids[:100]), page(totals[1], ids[100:])]), "disputes"
    )
    assert len(result) == 101


def test_changed_total_does_not_infer_complete_snapshot():
    ids = ["disp_" + str(i) for i in range(101)]
    with pytest.raises(HTTPException) as error:
        collection(Provider([page(101, ids[:100]), page(102, ids[100:])]), "disputes")
    assert error.value.status_code == 503
    assert (
        error.value.detail == "Financial provider collection unavailable [disputes:total_changed]"
    )


def test_staging_null_compat_cannot_hide_unretrieved_disputes(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("RAZORPAY_TEST_NULL_DISPUTES_COMPAT", "1")
    provider = Provider(
        [
            page(101, ["disp_" + str(i) for i in range(100)]),
            {"entity": "collection", "count": None, "items": None},
        ]
    )
    provider.RAZORPAY_KEY_ID = "rzp_test_synthetic"
    with pytest.raises(HTTPException) as error:
        collection(provider, "disputes")
    assert (
        error.value.detail
        == "Financial provider collection unavailable [disputes:incomplete_collection]"
    )


def test_staging_null_compat_does_not_apply_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("RAZORPAY_TEST_NULL_DISPUTES_COMPAT", "1")
    provider = Provider([{"entity": "collection", "count": None, "items": None}])
    provider.RAZORPAY_KEY_ID = "rzp_test_synthetic"
    with pytest.raises(HTTPException) as error:
        collection(provider, "disputes")
    assert error.value.detail == "Financial provider collection unavailable [disputes:count_type]"


class OrderPaymentsProvider:
    def __init__(self, count):
        self.count = count
        self.calls = []

    def call_razorpay(self, method, path):
        self.calls.append((method, path))
        assert (method, path) == ("GET", "orders/order_synthetic/payments")
        return 200, {
            "entity": "collection",
            "count": self.count,
            "items": [
                {
                    "entity": "payment",
                    "id": "pay_synthetic",
                    "order_id": "order_synthetic",
                    "status": "captured",
                }
            ],
        }


def test_partial_order_payments_cannot_establish_a_unique_capture():
    provider = OrderPaymentsProvider(count=2)
    with pytest.raises(HTTPException) as error:
        capture_id(provider, "order_synthetic")
    assert error.value.status_code == 503
    assert error.value.detail == (
        "Financial provider collection unavailable [order_payments:incomplete_collection]"
    )
    assert provider.calls == [("GET", "orders/order_synthetic/payments")]


def test_complete_order_payments_can_establish_a_unique_capture():
    provider = OrderPaymentsProvider(count=1)
    assert capture_id(provider, "order_synthetic") == "pay_synthetic"
