"""Synthetic provider used only by isolated financial tests; no network access."""

from copy import deepcopy
from urllib.parse import parse_qs
from uuid import uuid4


class Provider:
    def __init__(self):
        self.prefix = uuid4().hex
        self.orders = {}
        self.payments = {}
        self.refunds = {}
        self.disputes = {}
        self.calls = []
        self.timeout_create = False
        self.malformed_collections = set()

    def call(self, method, path, payload=None):
        self.calls.append((method, path, deepcopy(payload)))
        if method == "POST" and path == "orders":
            order = deepcopy(payload)
            order.update(
                id="order_Test" + self.prefix + str(len(self.orders) + 1),
                entity="order",
                status="created",
                amount_paid=0,
                attempts=0,
                amount_due=order["amount"],
            )
            self.orders[order["id"]] = order
            if self.timeout_create:
                raise TimeoutError("Synthetic ambiguous provider result")
            return 200, deepcopy(order)
        path, _, query = path.partition("?")
        if path in self.malformed_collections:
            return 200, dict(entity="collection", items=None, count=None)
        parts = path.split("/")
        if path == "disputes":
            items = list(self.disputes.values())
        elif len(parts) == 3 and parts[0] == "orders" and parts[2] == "payments":
            items = [item for item in self.payments.values() if item["order_id"] == parts[1]]
        elif len(parts) == 3 and parts[0] == "payments" and parts[2] == "refunds":
            items = [item for item in self.refunds.values() if item["payment_id"] == parts[1]]
        elif len(parts) == 2:
            store = {
                "orders": self.orders,
                "payments": self.payments,
                "refunds": self.refunds,
                "disputes": self.disputes,
            }.get(parts[0], {})
            return (200, deepcopy(store[parts[1]])) if parts[1] in store else (404, {})
        else:
            return 404, {}
        params = parse_qs(query)
        skip = int(params.get("skip", [0])[0])
        count = int(params.get("count", [100])[0])
        items = items[skip : skip + count]
        return 200, dict(entity="collection", count=len(items), items=deepcopy(items))

    def capture(self, order_id, payment_id=None):
        payment_id = payment_id or "pay_Test" + self.prefix + str(len(self.payments) + 1)
        order = self.orders[order_id]
        payment = dict(
            id=payment_id,
            entity="payment",
            order_id=order_id,
            amount=order["amount"],
            currency="INR",
            status="captured",
            captured=True,
            amount_refunded=0,
            refund_status=None,
        )
        self.payments[payment_id] = payment
        order["status"] = "paid"
        order["attempts"] = 1
        order["amount_paid"] = sum(
            item["amount"]
            for item in self.payments.values()
            if item["order_id"] == order_id and item["status"] == "captured"
        )
        order["amount_due"] = max(0, order["amount"] - order["amount_paid"])
        return payment_id
