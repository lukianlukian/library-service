import stripe
from celery import shared_task
from django.conf import settings
from rest_framework.exceptions import APIException

from payments.models import Payment
from payments.services import test_api_key, validate_session, verify_payment


@shared_task(ignore_result=True)
def check_payment_sessions():
    if not settings.STRIPE_SECRET_KEY.startswith("sk_test_"):
        return {"expired": 0, "paid": 0, "failed": 0}
    counts = {"expired": 0, "paid": 0, "failed": 0}
    # Finish the read cursor before network calls, including on SQLite.
    payments = list(
        Payment.objects.filter(status=Payment.Status.PENDING, session_id__isnull=False)
    )
    for payment in payments:
        try:
            session = stripe.checkout.Session.retrieve(
                payment.session_id, api_key=test_api_key()
            )
            validate_session(payment, session)
            if (
                session.get("payment_status") == "paid"
                and session.get("status") == "complete"
            ):
                verify_payment(payment, session=session)
                counts["paid"] += 1
            elif (
                session.get("status") == "expired"
                and session.get("payment_status") != "paid"
            ):
                counts["expired"] += Payment.objects.filter(
                    pk=payment.pk,
                    status=Payment.Status.PENDING,
                    session_id=payment.session_id,
                ).update(status=Payment.Status.EXPIRED)
        except stripe.StripeError, APIException:
            counts["failed"] += 1
    return counts
