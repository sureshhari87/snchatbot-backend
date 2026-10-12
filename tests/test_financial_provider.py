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


def test_collection_accepts_provider_total_count_for_single_page():
    assert collection(TotalCountProvider(), "orders/order_Test/payments", paginated=False) == [
        {"id": "pay_Test"}
    ]


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
