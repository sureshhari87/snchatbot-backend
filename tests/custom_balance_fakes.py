"""Synthetic provider only; never contacts Razorpay or reads credentials."""

from urllib.parse import urlsplit


class Provider:
    RAZORPAY_KEY_ID = "rzp_test_synthetic_balance"

    def __init__(self):
        self.orders = {}
        self.payments = {}
        self.refunds = {}
        self.posts = 0
        self.unknown = False
        self.uncertain = False
        self.outage = False

    def razorpay_checkout_is_configured(self):
        return True

    def razorpay_checkout_signature_is_valid(self, o, p, s):
        return s == "0" * 64

    def create_customer_action_lead(self, *args, **kwargs):
        return None

    def call_razorpay(self, method, path, payload=None):
        if self.outage:
            raise OSError("Synthetic unavailable provider")
        route = urlsplit("https://synthetic.test/" + path).path.lstrip("/")
        if method == "POST":
            assert route == "orders"
            self.posts += 1
            oid = "order_balance" + str(self.posts)
            row = dict(
                payload,
                id=oid,
                entity="order",
                status="created",
                amount_paid=0,
                amount_due=payload["amount"],
                attempts=0,
            )
            self.orders[oid] = row
            if self.unknown:
                raise OSError("Synthetic uncertain create")
            return 200, dict(row)
        assert method == "GET"
        if route == "disputes":
            return 200, (
                {"entity": "collection"}
                if self.uncertain
                else {"entity": "collection", "count": 0, "items": []}
            )
        if route.startswith("orders/") and route.endswith("/payments"):
            oid = route.split("/")[1]
            rows = [dict(p) for p in self.payments.values() if p["order_id"] == oid]
            return 200, {"entity": "collection", "count": len(rows), "items": rows}
        if route.startswith("payments/") and route.endswith("/refunds"):
            return 200, {"entity": "collection", "count": 0, "items": []}
        if route.startswith("orders/"):
            return 200, dict(self.orders[route.split("/")[1]])
        if route.startswith("payments/"):
            return 200, dict(self.payments[route.split("/")[1]])
        if route.startswith("refunds/"):
            return 200, dict(self.refunds[route.split("/")[1]])
        return 404, {}

    def payment(self, oid, status="captured"):
        order = self.orders[oid]
        pid = "pay_balance" + str(len(self.payments) + 1)
        order["attempts"] += 1
        if status == "captured":
            order.update(status="paid", amount_paid=order["amount"], amount_due=0)
        else:
            order.update(status="attempted")
        self.payments[pid] = dict(
            id=pid,
            entity="payment",
            order_id=oid,
            amount=order["amount"],
            currency="INR",
            status=status,
            captured=status == "captured",
            amount_refunded=0,
            refund_status=None,
        )
        return pid
