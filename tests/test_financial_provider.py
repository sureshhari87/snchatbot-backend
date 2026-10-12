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
    assert collection(
        TotalCountProvider(), "orders/order_Test/payments", paginated=False
    ) == [{"id": "pay_Test"}]
