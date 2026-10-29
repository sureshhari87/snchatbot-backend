"""Support-confirmed TEST envelopes cannot weaken financial evidence."""

import pytest
from fastapi import HTTPException

from financial_provider import captured, collection


@pytest.fixture(autouse=True)
def compat(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("RAZORPAY_TEST_NULL_DISPUTES_COMPAT", "1")


class Provider:
    RAZORPAY_KEY_ID = "rzp_test_synthetic"

    def __init__(self, envelope=None):
        self.envelope = (
            {"entity": "collection", "has_more": False} if envelope is None else envelope
        )
        self.paths = []

    def call_razorpay(self, method, path):
        assert method == "GET"
        self.paths.append(path)
        if path == "payments/pay_Test":
            return 200, dict(
                entity="payment",
                id="pay_Test",
                order_id="order_Test",
                currency="INR",
                amount=50000,
                status="captured",
                captured=True,
                amount_refunded=0,
                refund_status=None,
            )
        if path.startswith("payments/pay_Test/refunds?"):
            return 200, dict(entity="collection", count=0, items=[])
        return 200, self.envelope


def capture(provider):
    return captured(
        provider,
        order=dict(status="paid", amount_paid=50000, amount_due=0),
        order_id="order_Test",
        payment_id="pay_Test",
        amount_paise=50000,
    )


def test_compact_empty_capture_uses_payment_specific_read_only_query():
    provider = Provider()
    payment, adverse = capture(provider)
    assert payment["id"] == "pay_Test" and adverse == []
    assert provider.paths[-1] == "disputes?payment_id=pay_Test&count=100&skip=0"


@pytest.mark.parametrize(
    "environment,key,flag",
    [
        ("production", "rzp_test_synthetic", "1"),
        ("staging", "rzp_live_synthetic", "1"),
        ("staging", "rzp_test_synthetic", "0"),
        ("development", "rzp_test_synthetic", "1"),
    ],
)
def test_compact_empty_requires_all_staging_guards(monkeypatch, environment, key, flag):
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("RAZORPAY_TEST_NULL_DISPUTES_COMPAT", flag)
    provider = Provider()
    provider.RAZORPAY_KEY_ID = key
    with pytest.raises(HTTPException) as error:
        collection(provider, "disputes?payment_id=pay_Test")
    assert error.value.status_code == 503


@pytest.mark.parametrize(
    "envelope",
    [
        {"entity": "collection"},
        {"entity": "collection", "has_more": True},
        {"entity": "collection", "has_more": 0},
        {"entity": "collection", "has_more": "false"},
        {"entity": "collection", "has_more": False, "error": {}},
        {"entity": "collection", "has_more": False, "unexpected": []},
        {"entity": "collection", "count": None, "items": None, "has_more": True},
        {"entity": "collection", "count": 0, "items": [], "has_more": True},
    ],
)
def test_ambiguous_disputes_and_more_pages_are_never_empty(envelope):
    with pytest.raises(HTTPException) as error:
        collection(Provider(envelope), "disputes?payment_id=pay_Test")
    assert error.value.status_code == 503
    assert "pay_Test" not in error.value.detail


@pytest.mark.parametrize("path", ["payments/pay_Other/refunds", "orders/order_Test/payments"])
def test_compact_shape_is_not_payment_or_refund_evidence(path):
    with pytest.raises(HTTPException):
        collection(Provider(), path)


def test_live_capture_keeps_existing_global_disputes_path():
    provider = Provider(dict(entity="collection", count=0, items=[]))
    provider.RAZORPAY_KEY_ID = "rzp_live_synthetic"
    assert capture(provider)[1] == []
    assert provider.paths[-1] == "disputes?count=100&skip=0"


def test_another_payment_in_filtered_disputes_requires_review():
    provider = Provider(
        dict(
            entity="collection",
            count=1,
            items=[
                dict(
                    entity="dispute",
                    id="disp_Test",
                    payment_id="pay_Other",
                    currency="INR",
                    amount=50000,
                )
            ],
        )
    )
    with pytest.raises(HTTPException) as error:
        capture(provider)
    assert error.value.status_code == 503


def test_bound_dispute_remains_adverse_instead_of_automatic_release():
    provider = Provider(
        dict(
            entity="collection",
            count=1,
            items=[
                dict(
                    entity="dispute",
                    id="disp_Test",
                    payment_id="pay_Test",
                    currency="INR",
                    amount=50000,
                )
            ],
        )
    )
    adverse = capture(provider)[1]
    assert len(adverse) == 1 and adverse[0][0] == "dispute"


def test_compact_page_cannot_hide_an_advertised_record():
    class Pages(Provider):
        def call_razorpay(self, method, path):
            assert method == "GET"
            self.paths.append(path)
            if len(self.paths) == 1:
                return 200, dict(
                    entity="collection",
                    count=101,
                    items=[{"id": "disp_" + str(i)} for i in range(100)],
                )
            return 200, self.envelope

    with pytest.raises(HTTPException) as error:
        collection(Pages(), "disputes")
    assert (
        error.value.detail
        == "Financial provider collection unavailable [disputes:incomplete_collection]"
    )
