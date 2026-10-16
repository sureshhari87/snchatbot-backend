"""Saved-evidence reports must be private, exact, permission-scoped and read-only."""

import json
from dataclasses import asdict
from datetime import timedelta

import pytest
from sqlalchemy import event

import config
import financial_reporting as reports
import main
from models import OrderSnapshot, utc_now
from rewards_vouchers_models import (
    GiftVoucher,
    GiftVoucherEntry,
    RewardAccount,
    RewardEntry,
    RewardVoucherHold,
)
from rewards_vouchers_reservation_models import FinancialReservation, FinancialReservationEvent
from rewards_vouchers_rules import calculate_checkout
from savings import gold_credit
from savings_models import (
    SavingsAudit,
    SavingsEntry,
    SavingsGoldRate,
    SavingsHold,
    SavingsHoldDecision,
    SavingsPayment,
    SavingsScheme,
)
from voucher_funding_models import VoucherFunding, VoucherFundingEvent


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(reports, "ENABLED", True)
    monkeypatch.setattr(config, "APP_ENV", "test")

    def prohibited(*args, **kwargs):
        raise AssertionError("Reporting must not contact a provider")

    monkeypatch.setattr(main, "call_razorpay", prohibited)
    monkeypatch.setattr(main, "fetch_razorpay_payment", prohibited)


def get(client, headers, area, query=""):
    return client.get("/admin/financial-reconciliation/" + area + query, headers=headers)


def scheme_payment(db, user, actor, posted=False, amount=50000, gold=False, audit=True):
    now = utc_now()
    scheme = SavingsScheme(
        user_id=user.id,
        request_key="synthetic-plan",
        kind="digigold" if gold else "monthly",
        monthly_paise=None if gold else amount,
        state="active",
        started_at=now - timedelta(days=10),
        matures_at=now + timedelta(days=330),
    )
    db.add(scheme)
    db.flush()
    payment = SavingsPayment(
        scheme_id=scheme.id,
        user_id=user.id,
        request_key="synthetic-payment",
        amount_paise=amount,
        mode="manual",
        state="posted" if posted else "pending",
        installment=None if gold else 1,
        created_at=now,
        posted_at=now if posted else None,
        verified_reference="private-receipt" if posted else None,
    )
    db.add(payment)
    db.flush()
    if posted:
        rate = None
        purchased, benefit = 0, 0
        if gold:
            rate = SavingsGoldRate(
                paise_per_gram=1525000,
                source="Synthetic rate",
                effective_at=now - timedelta(minutes=1),
                valid_until=now + timedelta(minutes=10),
                purity="24K 995",
                created_by=actor.id,
                created_at=now,
            )
            db.add(rate)
            db.flush()
            purchased, benefit = gold_credit(amount, rate.paise_per_gram, 500)
        db.add(
            SavingsEntry(
                scheme_id=scheme.id,
                event_key=f"payment:{payment.id}",
                payment_id=payment.id,
                kind="credit",
                principal_paise=amount,
                gold_micrograms=purchased,
                bonus_micrograms=benefit,
                store_bonus_paise=0,
                rate_id=rate.id if rate else None,
                bonus_bps=500 if gold else 0,
                actor_id=actor.id,
                evidence_reference="private-receipt",
                created_at=now,
            )
        )
        if audit:
            db.add(
                SavingsAudit(
                    scheme_id=scheme.id,
                    event_key=f"post:{payment.id}",
                    action="payment_posted",
                    actor_id=actor.id,
                    evidence_reference="private-receipt",
                    created_at=now,
                )
            )
    db.commit()
    return scheme, payment


def merchandise(db, user, posted=True, voucher=None, points=0):
    number = db.query(FinancialReservation).count() + 1
    quote = asdict(
        calculate_checkout(
            merchandise_paise=100000,
            voucher_available_paise=5000 if voucher else 0,
            reward_points_available=points,
            reward_points_requested=points,
        )
    )
    order = None
    if posted:
        order = OrderSnapshot(
            user_id=user.id,
            order_reference=f"order_private{number}",
            status="placed",
            payment_status="verified",
            payment_reference=f"pay_private{number}",
            total=1000.0,
            currency="INR",
        )
        db.add(order)
        db.flush()
    row = FinancialReservation(
        user_id=user.id,
        request_key=f"request-{number}",
        request_sha256="0" * 64,
        receipt=f"receipt-{number}",
        quote_json=json.dumps(quote),
        payable_paise=quote["payable_paise"],
        reward_points=points,
        voucher_id=voucher.id if voucher else None,
        voucher_paise=quote["voucher_redemption_paise"],
        state="posted" if posted else "reserved",
        order_id=order.id if order else None,
        provider_order_id=order.order_reference if order else None,
        provider_payment_id=order.payment_reference if order else None,
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    evidence = None
    if posted:
        evidence = reports.digest(
            dict(
                reservation_id=row.id,
                order_id=order.id,
                payment_id=row.provider_payment_id,
                quote=quote,
            )
        )
        db.add(
            FinancialReservationEvent(
                reservation_id=row.id,
                event_key=f"post-{number}",
                action="payment_posted",
                evidence_sha256=reports.digest(
                    dict(payment_id=row.provider_payment_id, evidence_sha256=evidence)
                ),
                created_at=utc_now(),
            )
        )
    db.commit()
    return row, evidence, quote


def voucher(db, user, actor, posted=True):
    now = utc_now()
    row = GiftVoucher(
        purchaser_id=user.id,
        assigned_user_id=None,
        request_key="voucher",
        code_hash="0" * 64,
        source="purchased",
        amount_paise=50000,
        balance_paise=50000 if posted else 0,
        reserved_paise=0,
        state="active" if posted else "pending",
        activated_at=now if posted else None,
        expires_at=now + timedelta(days=365) if posted else None,
        created_at=now,
    )
    db.add(row)
    db.flush()
    db.add(
        VoucherFunding(
            voucher_id=row.id,
            request_sha256="0" * 64,
            payment_mode="manual",
            code_ciphertext="private-voucher-code",
            receipt="private-voucher-receipt",
            state="posted" if posted else "not_started",
            created_at=now,
        )
    )
    if posted:
        proof = reports.digest(
            dict(
                voucher_id=row.id,
                amount_paise=50000,
                actor_id=actor.id,
                reference="private-receipt",
                reason="Synthetic receipt verified",
                kind="manual_funding",
            )
        )
        db.add(
            GiftVoucherEntry(
                voucher_id=row.id,
                event_key=f"voucher-fund:{row.id}",
                kind="manual_funding",
                amount_delta_paise=50000,
                actor_id=actor.id,
                reason="Synthetic receipt verified",
                evidence_reference="private-receipt",
                evidence_sha256=proof,
                created_at=now,
            )
        )
        db.add(
            VoucherFundingEvent(
                voucher_id=row.id,
                action="funding_posted",
                actor_id=actor.id,
                evidence_sha256=proof,
                created_at=now,
            )
        )
    db.commit()
    return row


@pytest.mark.parametrize("area", reports.AREAS)
def test_reports_require_admin_and_no_store(client, admin_headers, auth_headers, area):
    assert get(client, {}, area).status_code == 401
    assert get(client, auth_headers, area).status_code == 403
    response = get(client, admin_headers, area)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["read_only"] is True and body["provider_verified"] is False
    assert body["as_of_ist"].endswith("+05:30")
    assert body["items"] == []
    assert "total" not in body and "revenue" not in body


def test_scopes_use_existing_domain_permissions(client, admin_headers, admin_user, monkeypatch):
    monkeypatch.setattr(
        main,
        "user_permissions",
        lambda user: {"savings:manage"} if user.id == admin_user.id else set(),
    )
    assert get(client, admin_headers, "savings").status_code == 200
    for area in set(reports.AREAS) - {"savings"}:
        assert get(client, admin_headers, area).status_code == 403


@pytest.mark.parametrize(
    "environment,flag,status",
    [("test", False, 503), ("production", True, 403), ("staging", True, 200)],
)
def test_default_off_and_environment_boundary(
    client, admin_headers, monkeypatch, environment, flag, status
):
    monkeypatch.setattr(reports, "ENABLED", flag)
    monkeypatch.setattr(config, "APP_ENV", environment)
    assert get(client, admin_headers, "savings").status_code == status


def test_integer_precision_and_cursor_pagination(
    client, db, admin_headers, verified_user, admin_user
):
    _, payment = scheme_payment(db, verified_user, admin_user, amount=9007199254740993)
    response = get(client, admin_headers, "savings", "?limit=1")
    item = response.json()["items"][0]
    assert item["measures"]["pending_principal_paise"] == "9007199254740993"
    assert item["status"] == "pending"
    assert get(client, admin_headers, "savings", f"?after={payment.id}").json()["items"] == []
    for query in ["?limit=0", "?limit=51", "?after=-1"]:
        assert get(client, admin_headers, "savings", query).status_code == 422


def test_savings_posted_requires_matching_audit(
    client, db, admin_headers, verified_user, admin_user
):
    scheme_payment(db, verified_user, admin_user, posted=True, audit=False)
    item = get(client, admin_headers, "savings").json()["items"][0]
    assert item["status"] == "requires_review" and item["evidence_valid"] is False
    assert all(value is None for value in item["measures"].values())


def test_digigold_and_released_hold_are_read_only(
    client, db, admin_headers, verified_user, admin_user
):
    scheme, payment = scheme_payment(
        db, verified_user, admin_user, posted=True, amount=10000, gold=True
    )
    hold = SavingsHold(
        scheme_id=scheme.id,
        payment_id=payment.id,
        kind="admin_review",
        reference="private-hold",
        reason="Synthetic hold",
        actor_id=admin_user.id,
        created_at=utc_now(),
    )
    db.add(hold)
    db.commit()
    item = get(client, admin_headers, "savings").json()["items"][0]
    assert item["held"] is True
    assert item["measures"]["gold_micrograms"] == "6557"
    assert item["measures"]["benefit_micrograms"] == "327"
    db.add(
        SavingsHoldDecision(
            hold_id=hold.id,
            action="released",
            event_key="released",
            reason="Synthetic verified release",
            actor_id=admin_user.id,
            evidence_sha256="0" * 64,
            created_at=utc_now(),
        )
    )
    db.commit()
    assert get(client, admin_headers, "savings").json()["items"][0]["held"] is False


def test_merchandise_counts_saved_receipts_not_legacy_float_totals(
    client, db, admin_headers, verified_user
):
    row, _, _ = merchandise(db, verified_user)
    db.add(
        OrderSnapshot(
            user_id=verified_user.id,
            order_reference="unreconciled-legacy",
            total=123.456,
            currency="INR",
            payment_status="paid",
        )
    )
    db.commit()
    result = get(client, admin_headers, "merchandise").json()
    assert result["items"][0]["measures"]["posted_payment_paise"] == str(row.payable_paise)
    assert result["legacy_orders_requiring_review"] == 1
    assert "private" not in json.dumps(result)


def test_pending_merchandise_and_invalid_quote_do_not_show_paid(
    client, db, admin_headers, verified_user
):
    row, _, _ = merchandise(db, verified_user, posted=False)
    item = get(client, admin_headers, "merchandise").json()["items"][0]
    assert item["status"] == "pending" and item["measures"]["posted_payment_paise"] == "0"
    quote = json.loads(row.quote_json)
    quote["payable_paise"] += 1
    row.quote_json = json.dumps(quote)
    db.commit()
    assert (
        get(client, admin_headers, "merchandise").json()["items"][0]["status"] == "requires_review"
    )


def test_voucher_funding_and_redemption_are_separate_and_private(
    client, db, admin_headers, verified_user, admin_user
):
    row = voucher(db, verified_user, admin_user)
    reservation, evidence, _ = merchandise(db, verified_user, voucher=row)
    db.add(
        GiftVoucherEntry(
            voucher_id=row.id,
            order_id=reservation.order_id,
            event_key="redemption",
            kind="redemption",
            amount_delta_paise=-5000,
            evidence_reference=reservation.provider_payment_id,
            evidence_sha256=evidence,
            created_at=utc_now(),
        )
    )
    row.balance_paise -= 5000
    db.add(
        RewardVoucherHold(
            voucher_id=row.id,
            kind="admin_review",
            reference="hold",
            reason="Synthetic review",
            actor_id=admin_user.id,
            created_at=utc_now(),
        )
    )
    db.commit()
    result = get(client, admin_headers, "vouchers").json()
    item = result["items"][0]
    assert item["held"] is True
    assert item["measures"] == dict(
        funding_paise="50000",
        complimentary_paise="0",
        pending_funding_paise="0",
        redemption_paise="5000",
        reserved_paise="0",
    )
    assert "private" not in json.dumps(result)


def test_pending_voucher_has_no_posted_funding(
    client, db, admin_headers, verified_user, admin_user
):
    voucher(db, verified_user, admin_user, posted=False)
    item = get(client, admin_headers, "vouchers").json()["items"][0]
    assert item["status"] == "pending" and item["measures"]["funding_paise"] == "0"


def test_reward_points_require_ledger_and_posting_evidence(
    client, db, admin_headers, verified_user
):
    reservation, evidence, quote = merchandise(db, verified_user)
    account = RewardAccount(
        user_id=verified_user.id,
        balance_points=quote["reward_points_earned"],
        reserved_points=0,
        created_at=utc_now(),
    )
    db.add(account)
    db.flush()
    db.add(
        RewardEntry(
            user_id=verified_user.id,
            order_id=reservation.order_id,
            event_key="earned",
            kind="purchase_credit",
            points_delta=quote["reward_points_earned"],
            evidence_reference=reservation.provider_payment_id,
            evidence_sha256=evidence,
            created_at=utc_now(),
        )
    )
    db.commit()
    item = get(client, admin_headers, "rewards").json()["items"][0]
    assert item["measures"] == dict(issued_points="10", used_points="0", reserved_points="0")
    account.balance_points += 1
    db.commit()
    assert get(client, admin_headers, "rewards").json()["items"][0]["status"] == "requires_review"


@pytest.mark.parametrize("area", reports.AREAS)
def test_repeated_reads_never_issue_financial_writes(
    client, db, admin_headers, verified_user, admin_user, area
):
    scheme_payment(db, verified_user, admin_user, posted=True)
    voucher(db, verified_user, admin_user)
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].lower())

    event.listen(db.get_bind(), "before_cursor_execute", observe)
    try:
        first = get(client, admin_headers, area)
        second = get(client, admin_headers, area)
        assert first.status_code == second.status_code == 200
        assert first.json()["items"] == second.json()["items"]
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe)
    assert not ({"insert", "update", "delete", "replace"} & set(statements))


def test_post_methods_are_not_exposed(client, admin_headers):
    assert (
        client.post("/admin/financial-reconciliation/savings", headers=admin_headers).status_code
        == 405
    )


def test_custom_balance_pending_posted_and_duplicate_confirm_stay_separate(
    client, db, auth_headers, admin_headers, monkeypatch
):
    from test_custom_design_balances import manual, request, setup

    import custom_design_api
    import custom_design_balance

    monkeypatch.setattr(custom_design_api, "ENABLED", True)
    monkeypatch.setattr(custom_design_balance, "ENABLED", True)
    design, quote = setup(client, auth_headers, admin_headers)
    response = request(client, auth_headers, design, quote)
    assert response.status_code == 200, response.text
    request_id = response.json()["balance_request"]["id"]
    pending = get(client, admin_headers, "custom_designs").json()["items"][0]
    assert pending["status"] == "pending"
    assert pending["measures"]["pending_balance_paise"] == str(quote["total_paise"])
    assert manual(client, admin_headers, design, request_id).status_code == 200
    assert manual(client, admin_headers, design, request_id).status_code == 200
    posted = get(client, admin_headers, "custom_designs").json()["items"][0]
    assert posted["evidence_valid"] is True
    assert posted["measures"]["posted_balance_paise"] == str(quote["total_paise"])
    assert posted["measures"]["posted_advance_paise"] == "0"
    assert posted["measures"]["pending_balance_paise"] == "0"


def test_cursor_pages_do_not_duplicate_pending_payments(
    client, db, admin_headers, verified_user, admin_user
):
    scheme, _ = scheme_payment(db, verified_user, admin_user)
    for index in [2, 3]:
        db.add(
            SavingsPayment(
                scheme_id=scheme.id,
                user_id=verified_user.id,
                request_key=f"pending-{index}",
                amount_paise=50000,
                mode="manual",
                state="pending",
                installment=index,
                created_at=utc_now(),
            )
        )
    db.commit()
    first = get(client, admin_headers, "savings", "?limit=2").json()
    second = get(client, admin_headers, "savings", f"?after={first['next_after']}&limit=2").json()
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert second["next_after"] is None
    assert not ({row["id"] for row in first["items"]} & {row["id"] for row in second["items"]})


def test_database_failure_is_unavailable_not_zero_or_raw_error(client, admin_headers, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    def unavailable(*args, **kwargs):
        raise SQLAlchemyError("private-provider-response")

    monkeypatch.setattr(reports, "report", unavailable)
    response = get(client, admin_headers, "savings")
    assert response.status_code == 503
    assert "private-provider-response" not in response.text
    assert "unavailable" in response.text


@pytest.mark.parametrize(
    "value,expected",
    [
        (9007199254740993, 9007199254740993),
        ("decimal", 9007199254740993),
        (1.5, None),
        ("fractional_decimal", None),
    ],
)
def test_aggregate_conversion_is_exact_or_unavailable(value, expected):
    from decimal import Decimal
    from unittest.mock import Mock

    if value == "decimal":
        value = Decimal("9007199254740993")
    elif value == "fractional_decimal":
        value = Decimal("1.5")
    query = Mock()
    query.with_entities.return_value.scalar.return_value = value
    if expected is None:
        with pytest.raises(ValueError):
            reports.total(query, None)
    else:
        assert reports.total(query, None) == expected


def test_custom_advance_hold_preserves_posted_history_and_checks_hash(
    client, db, auth_headers, admin_headers, admin_user, monkeypatch
):
    from test_custom_design_balances import setup

    import custom_design_api
    from custom_design_models import CustomDesignAudit
    from custom_design_payments import CustomDesignAdvance, CustomDesignHold

    monkeypatch.setattr(custom_design_api, "ENABLED", True)
    design, quote = setup(client, auth_headers, admin_headers, advance=10000)
    db.add(
        CustomDesignAdvance(
            design_id=design["id"],
            quote_id=quote["id"],
            amount_paise=10000,
            provider_order_id="order_private_advance",
            provider_payment_id="pay_private_advance",
            created_at=utc_now(),
        )
    )
    proof = CustomDesignAudit(
        design_id=design["id"],
        quote_id=quote["id"],
        event_key="advance-posted",
        action="advance_posted",
        actor_id=None,
        payload_sha256=reports.digest([quote["id"], "pay_private_advance", 10000]),
        created_at=utc_now(),
    )
    db.add(proof)
    db.add(
        CustomDesignHold(
            design_id=design["id"],
            reference="synthetic-hold",
            reason="Audited review",
            actor_id=admin_user.id,
            created_at=utc_now(),
        )
    )
    db.commit()
    item = get(client, admin_headers, "custom_designs").json()["items"][0]
    assert item["held"] is True
    assert item["measures"]["posted_advance_paise"] == "10000"
    proof.payload_sha256 = "f" * 64
    db.commit()
    item = get(client, admin_headers, "custom_designs").json()["items"][0]
    assert item["status"] == "requires_review" and item["held"] is None
    assert all(value is None for value in item["measures"].values())


def test_reward_usage_and_reservations_are_separate(client, db, admin_headers, verified_user):
    account = RewardAccount(
        user_id=verified_user.id, balance_points=0, reserved_points=0, created_at=utc_now()
    )
    db.add(account)
    db.commit()
    for spent in [0, 2]:
        reservation, evidence, quote = merchandise(db, verified_user, points=spent)
        if spent:
            db.add(
                RewardEntry(
                    user_id=verified_user.id,
                    order_id=reservation.order_id,
                    event_key=f"spent:{reservation.id}",
                    kind="redemption",
                    points_delta=-spent,
                    evidence_reference=reservation.provider_payment_id,
                    evidence_sha256=evidence,
                    created_at=utc_now(),
                )
            )
        db.add(
            RewardEntry(
                user_id=verified_user.id,
                order_id=reservation.order_id,
                event_key=f"earned:{reservation.id}",
                kind="purchase_credit",
                points_delta=quote["reward_points_earned"],
                evidence_reference=reservation.provider_payment_id,
                evidence_sha256=evidence,
                created_at=utc_now(),
            )
        )
        account.balance_points += quote["reward_points_earned"] - spent
        db.commit()
    merchandise(db, verified_user, posted=False, points=3)
    account.reserved_points = 3
    db.commit()
    item = get(client, admin_headers, "rewards").json()["items"][0]
    assert item["measures"] == dict(issued_points="19", used_points="2", reserved_points="3")


@pytest.mark.parametrize("mode", ["razorpay", "complimentary"])
def test_online_and_complimentary_funding_have_distinct_audited_measures(
    client, db, admin_headers, verified_user, admin_user, mode
):
    row = voucher(db, verified_user, admin_user, posted=False)
    funding = db.get(VoucherFunding, row.id)
    now = utc_now()
    funding.payment_mode = mode
    funding.state = "posted"
    row.state = "active"
    row.source = "complimentary" if mode == "complimentary" else "purchased"
    row.balance_paise = row.amount_paise
    row.activated_at = now
    row.expires_at = now + timedelta(days=365)
    kind = "razorpay_funding" if mode == "razorpay" else "complimentary"
    actor = None if mode == "razorpay" else admin_user.id
    reason = None if mode == "razorpay" else "Synthetic complimentary approval"
    reference = "pay_private_voucher" if mode == "razorpay" else "synthetic-complimentary"
    if mode == "razorpay":
        funding.provider_order_id = "order_private_voucher"
        funding.provider_payment_id = reference
        evidence = dict(
            voucher_id=row.id,
            payment_id=reference,
            provider_order_id=funding.provider_order_id,
            amount_paise=row.amount_paise,
        )
    else:
        evidence = dict(
            voucher_id=row.id,
            amount_paise=row.amount_paise,
            actor_id=actor,
            reference=reference,
            reason=reason,
            kind=kind,
        )
    proof = reports.digest(evidence)
    db.add(
        GiftVoucherEntry(
            voucher_id=row.id,
            event_key=f"voucher-fund:{row.id}",
            kind=kind,
            amount_delta_paise=row.amount_paise,
            actor_id=actor,
            reason=reason,
            evidence_reference=reference,
            evidence_sha256=proof,
            created_at=now,
        )
    )
    posting = VoucherFundingEvent(
        voucher_id=row.id,
        action="funding_posted",
        actor_id=actor,
        evidence_sha256=proof,
        created_at=now,
    )
    db.add(posting)
    db.commit()
    item = get(client, admin_headers, "vouchers").json()["items"][0]
    assert item["evidence_valid"] is True
    assert item["measures"]["funding_paise"] == ("50000" if mode == "razorpay" else "0")
    assert item["measures"]["complimentary_paise"] == ("50000" if mode == "complimentary" else "0")
    posting.evidence_sha256 = "f" * 64
    db.commit()
    assert get(client, admin_headers, "vouchers").json()["items"][0]["status"] == "requires_review"


def test_closed_savings_request_requires_saved_resolution(
    client, db, admin_headers, verified_user, admin_user
):
    from savings_models import SavingsPaymentResolution

    _, payment = scheme_payment(db, verified_user, admin_user)
    payment.state = "cancelled"
    payment.installment = None
    db.commit()
    assert get(client, admin_headers, "savings").json()["items"][0]["status"] == "requires_review"
    db.add(
        SavingsPaymentResolution(
            payment_id=payment.id,
            kind="cancelled",
            reason="Synthetic cancellation",
            original_installment=1,
            actor_id=admin_user.id,
            created_at=utc_now(),
        )
    )
    db.commit()
    item = get(client, admin_headers, "savings").json()["items"][0]
    assert item["status"] == "closed"
    assert item["measures"]["posted_principal_paise"] == "0"


def test_reward_posting_proof_queries_are_batched(client, db, admin_headers, verified_user):
    db.add(
        RewardAccount(
            user_id=verified_user.id, balance_points=200, reserved_points=0, created_at=utc_now()
        )
    )
    db.commit()
    for number in range(20):
        reservation, evidence, quote = merchandise(db, verified_user)
        db.add(
            RewardEntry(
                user_id=verified_user.id,
                order_id=reservation.order_id,
                event_key=f"batch-earned:{number}",
                kind="purchase_credit",
                points_delta=quote["reward_points_earned"],
                evidence_reference=reservation.provider_payment_id,
                evidence_sha256=evidence,
                created_at=utc_now(),
            )
        )
        db.commit()
    reads = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().lower().startswith("select"):
            reads.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", observe)
    try:
        response = get(client, admin_headers, "rewards")
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["evidence_valid"] is True
    assert item["measures"]["issued_points"] == "200"
    assert len(reads) <= 12
