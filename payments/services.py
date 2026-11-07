from datetime import timedelta
from decimal import Decimal

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from payments.models import Payment
from notifications.services import schedule_event


class PaymentUnavailable(APIException):
    status_code = 503
    default_detail = "Stripe unavailable. Retry with the same Idempotency-Key."


class IdempotencyConflict(APIException):
    status_code = 409
    default_detail = "Idempotency-Key belongs to a different request."


def test_api_key():
    key = settings.STRIPE_SECRET_KEY
    if not key.startswith("sk_test_"):
        raise PaymentUnavailable(
            "Configure a Stripe sk_test_ key; live keys are refused."
        )
    return key


def rental_amount(daily_fee, days):
    amount = (Decimal(daily_fee) * Decimal(max(1, days))).quantize(Decimal("0.01"))
    # Stripe's USD card minimum is 50 cents; do not reserve an unpayable rental.
    if amount < Decimal("0.50") or amount > Decimal("999999.99"):
        raise ValidationError(
            {"book": "Rental total must be between 0.50 and 999999.99 USD."}
        )
    return amount


def checkout_parameters(payment):
    base = settings.STRIPE_PUBLIC_BASE_URL.rstrip("/")
    return {
        "mode": "payment",
        "payment_method_types": ["card"],
        "line_items": [
            {
                "price_data": {
                    "currency": payment.currency,
                    "unit_amount": int(payment.money_to_pay * Decimal("100")),
                    "product_data": {"name": "Library rental"},
                },
                "quantity": 1,
            }
        ],
        "client_reference_id": str(payment.pk),
        "metadata": {"payment_id": str(payment.pk)},
        "success_url": base + "/api/payments/success/?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": base + "/api/payments/cancel/?session_id={CHECKOUT_SESSION_ID}",
    }


def ensure_checkout(payment):
    if payment.session_id:
        return payment
    # Stripe may prune idempotency keys after 24h. Never risk creating another
    # payable session when an old attempt has an unknown outcome.
    if timezone.now() - payment.created_at >= timedelta(hours=23):
        raise IdempotencyConflict(
            "Checkout outcome needs reconciliation; automatic retry window expired."
        )
    key = test_api_key()
    try:
        session = stripe.checkout.Session.create(
            **payment.checkout_parameters,
            api_key=key,
            idempotency_key=f"rental-checkout-{payment.request_key}",
        )
    except stripe.StripeError:
        raise PaymentUnavailable()
    if session.get("livemode") is not False or not session.get("id", "").startswith(
        "cs_test_"
    ):
        raise PaymentUnavailable("Stripe returned a session outside test mode.")
    if not session.get("url"):
        raise PaymentUnavailable(
            "Stripe did not return a Checkout URL; retry the same request."
        )
    Payment.objects.filter(pk=payment.pk, session_id__isnull=True).update(
        session_id=session["id"], session_url=session["url"]
    )
    payment.refresh_from_db()
    return payment


def verify_payment(payment):
    if payment.status == Payment.Status.PAID:
        return payment
    try:
        session = stripe.checkout.Session.retrieve(
            payment.session_id, api_key=test_api_key()
        )
    except stripe.StripeError:
        raise PaymentUnavailable("Could not verify payment; retry this success URL.")
    if (
        session.get("id") != payment.session_id
        or session.get("livemode") is not False
        or session.get("mode") != "payment"
        or session.get("client_reference_id") != str(payment.pk)
        or session.get("metadata", {}).get("payment_id") != str(payment.pk)
        or session.get("amount_total") != int(payment.money_to_pay * Decimal("100"))
        or session.get("currency") != payment.currency
    ):
        raise ValidationError({"detail": "Stripe session does not match this payment."})
    if session.get("payment_status") != "paid" or session.get("status") != "complete":
        raise ValidationError({"detail": "Stripe has not confirmed payment."})
    # Atomic state transition; concurrent/repeated callbacks do no extra work.
    with transaction.atomic():
        changed = Payment.objects.filter(
            pk=payment.pk, status=Payment.Status.PENDING
        ).update(status=Payment.Status.PAID)
        if changed:
            schedule_event(
                f"payment:{payment.pk}",
                f"Payment #{payment.pk} confirmed: {payment.money_to_pay} {payment.currency.upper()}; borrowing #{payment.borrowing_id}.",
            )
    payment.refresh_from_db()
    return payment
