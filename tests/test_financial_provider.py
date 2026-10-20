import pytest
from fastapi import HTTPException

from financial_provider import collection


class TotalCountProvider:
    def call_razorpay(self, method, path):
        assert method == "GET"
        assert path == "orders/order_Test/payments"
        return 200, {
            "entity": "collection",
            "count": 2,
            "items": [{"id": "pay_Test"}],
        }


def test_collection_rejects_incomplete_provider_total_for_single_page():
    with pytest.raises(HTTPException) as caught:
        collection(TotalCountProvider(), "orders/order_Test/payments", paginated=False)
    assert caught.value.status_code == 503
    assert caught.value.detail == (
        "Financial provider collection unavailable [order_payments:incomplete_collection]"
    )


class MalformedDisputesProvider:
    def call_razorpay(self, method, path):
        return 200, {"entity": "collection", "count": None, "items": []}


def test_collection_error_identifies_safe_stage_and_reason_without_path_values():
    with pytest.raises(HTTPException) as caught:
        collection(MalformedDisputesProvider(), "disputes")
    assert caught.value.status_code == 503
    assert caught.value.detail == (
        "Financial provider collection unavailable [disputes:count_type]"
    )


class EmptyDisputesProvider:
    def call_razorpay(self, method, path):
        assert method == "GET"
        assert path == "disputes?count=100&skip=0"
        return 200, {"entity": "collection", "count": 0}


def test_collection_accepts_unambiguously_empty_disputes_without_items():
    assert collection(EmptyDisputesProvider(), "disputes") == []


class NullEmptyTestDisputesProvider:
    RAZORPAY_KEY_ID = "rzp_test_example"

    def call_razorpay(self, method, path):
        return 200, {"entity": "collection", "count": None, "items": None}


def test_collection_accepts_null_empty_disputes_for_staging_compat_only(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("RAZORPAY_TEST_NULL_DISPUTES_COMPAT", "1")
    assert collection(NullEmptyTestDisputesProvider(), "disputes") == []


class NullEmptyLiveDisputesProvider(NullEmptyTestDisputesProvider):
    RAZORPAY_KEY_ID = "rzp_live_example"


def test_collection_rejects_null_empty_disputes_for_live_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("RAZORPAY_TEST_NULL_DISPUTES_COMPAT", "1")
    with pytest.raises(HTTPException) as caught:
        collection(NullEmptyLiveDisputesProvider(), "disputes")
    assert caught.value.detail == (
        "Financial provider collection unavailable [disputes:count_type]"
    )


def test_collection_rejects_null_empty_test_disputes_without_explicit_compat():
    with pytest.raises(HTTPException) as caught:
        collection(NullEmptyTestDisputesProvider(), "disputes")
    assert caught.value.detail == (
        "Financial provider collection unavailable [disputes:count_type]"
    )


class EmptyOtherCollectionProvider:
    def call_razorpay(self, method, path):
        return 200, {"entity": "collection", "count": 0}


def test_missing_items_remains_invalid_for_other_collection_routes():
    with pytest.raises(HTTPException) as caught:
        collection(EmptyOtherCollectionProvider(), "payments/pay_Test/refunds")
    assert caught.value.detail == (
        "Financial provider collection unavailable [payment_refunds:items]"
    )
