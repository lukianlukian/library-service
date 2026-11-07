from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import stripe
from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from books.models import Book
from borrowings.models import Borrowing
from borrowings.services import borrow_book
from payments.models import CheckoutSession, Payment
from payments.services import ensure_checkout, renew_checkout
from payments.tasks import check_payment_sessions


@override_settings(STRIPE_SECRET_KEY="sk_test_mock")
class RenewalTests(APITransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="reader@example.test")
        self.other = get_user_model().objects.create_user(email="other@example.test")
        self.staff = get_user_model().objects.create_user(
            email="staff@example.test", is_staff=True
        )
        self.book = Book.objects.create(
            title="Dune", author="Herbert", inventory=2, daily_fee="1.00", cover="HARD"
        )
        self.borrowing = borrow_book(
            book=self.book,
            user=self.user,
            expected_return_date=timezone.localdate() + timedelta(days=3),
        )
        self.payment = self.borrowing.payments.get()
        with patch(
            "payments.services.stripe.checkout.Session.create",
            return_value={
                "id": "cs_test_old",
                "url": "https://checkout.stripe.com/old",
                "livemode": False,
            },
        ):
            ensure_checkout(self.payment)
        self.payment.status = "EXPIRED"
        self.payment.save(update_fields=["status"])
        self.client.force_authenticate(self.user)
        self.key = str(uuid4())
        self.client.credentials(HTTP_IDEMPOTENCY_KEY=self.key)
        self.url = reverse("payments:renew", args=[self.payment.pk])
        self.create = patch(
            "payments.services.stripe.checkout.Session.create",
            return_value={
                "id": "cs_test_new",
                "url": "https://checkout.stripe.com/new",
                "livemode": False,
            },
        ).start()
        self.retrieve = patch(
            "payments.services.stripe.checkout.Session.retrieve"
        ).start()
        self.addCleanup(patch.stopall)

    def session(self, **changes):
        return {
            "id": "cs_test_old",
            "livemode": False,
            "mode": "payment",
            "client_reference_id": str(self.payment.pk),
            "metadata": {"payment_id": str(self.payment.pk)},
            "amount_total": 300,
            "currency": "usd",
            "payment_status": "unpaid",
            "status": "expired",
            **changes,
        }

    def pending(self):
        self.payment.status = "PENDING"
        self.payment.save(update_fields=["status"])

    def assert_single_reservation(self):
        self.assertEqual(Borrowing.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.book.refresh_from_db()
        self.assertEqual(self.book.inventory, 1)

    def test_renewal_uses_original_amount_and_replay_reuses_session(self):
        def network(**kwargs):
            self.assertFalse(connection.in_atomic_block)
            return {
                "id": "cs_test_new",
                "url": "https://checkout.stripe.com/new",
                "livemode": False,
            }

        self.create.side_effect = network
        response = self.client.post(
            self.url, {"money_to_pay": "0.01", "status": "PAID"}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "PENDING")
        self.assertEqual(response.data["money_to_pay"], "3.00")
        self.assertEqual(response.data["session_id"], "cs_test_new")
        self.assertEqual(self.client.post(self.url).status_code, 200)
        self.create.assert_called_once()
        self.assertEqual(
            self.create.call_args.kwargs["idempotency_key"],
            f"rental-checkout-{self.key}",
        )
        self.assertEqual(CheckoutSession.objects.count(), 2)
        self.assert_single_reservation()

    def test_renewal_failure_retries_same_generation(self):
        self.create.side_effect = stripe.APIConnectionError("test failure")
        self.assertEqual(self.client.post(self.url).status_code, 503)
        original_params = dict(self.create.call_args.kwargs)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "PENDING")
        self.assertIsNone(self.payment.session_id)
        self.create.side_effect = None
        self.assertEqual(self.client.post(self.url).status_code, 200)
        self.assertEqual(self.create.call_args.kwargs, original_params)
        self.assert_single_reservation()

    def test_different_key_and_reused_generation_key_are_rejected(self):
        self.assertEqual(self.client.post(self.url).status_code, 200)
        self.client.credentials(HTTP_IDEMPOTENCY_KEY=str(uuid4()))
        self.assertEqual(self.client.post(self.url).status_code, 409)
        self.payment.refresh_from_db()
        self.payment.status = "EXPIRED"
        self.payment.save(update_fields=["status"])
        self.client.credentials(HTTP_IDEMPOTENCY_KEY=self.key)
        self.assertEqual(self.client.post(self.url).status_code, 409)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "EXPIRED")
        self.create.assert_called_once()

    def test_paid_and_ordinary_pending_cannot_be_renewed(self):
        for status in ("PAID", "PENDING"):
            self.payment.status = status
            self.payment.save(update_fields=["status"])
            self.assertEqual(self.client.post(self.url).status_code, 409)
        self.create.assert_not_called()
        self.assert_single_reservation()

    def test_authorization_and_staff_access(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(self.url).status_code, 401)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.create.assert_not_called()
        self.client.force_authenticate(self.staff)
        self.client.credentials(HTTP_IDEMPOTENCY_KEY=self.key)
        self.assertEqual(self.client.post(self.url).status_code, 200)

    def test_missing_key_is_rejected(self):
        self.client.credentials()
        self.assertEqual(self.client.post(self.url).status_code, 400)
        self.create.assert_not_called()

    def test_old_success_callback_cannot_change_new_session(self):
        self.assertEqual(self.client.post(self.url).status_code, 200)
        response = self.client.get(
            reverse("payments:success"), {"session_id": "cs_test_old"}
        )
        self.assertEqual(response.status_code, 404)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "PENDING")
        self.retrieve.assert_not_called()

    def test_expiry_checker_only_marks_verified_expired_sessions(self):
        self.pending()
        self.retrieve.return_value = self.session()
        self.assertEqual(check_payment_sessions.run()["expired"], 1)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "EXPIRED")
        self.assert_single_reservation()

    def test_open_or_invalid_session_and_network_errors_do_not_expire(self):
        self.pending()
        for changes in (
            {"status": "open"},
            {"livemode": True},
            {"currency": "eur"},
            {"amount_total": 1},
        ):
            self.retrieve.return_value = self.session(**changes)
            check_payment_sessions.run()
            self.payment.refresh_from_db()
            self.assertEqual(self.payment.status, "PENDING")
        self.retrieve.side_effect = stripe.APIConnectionError("timeout")
        self.assertEqual(check_payment_sessions.run()["failed"], 1)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "PENDING")

    def test_checker_reconciles_paid_and_does_not_recheck_it(self):
        self.pending()
        self.retrieve.return_value = self.session(
            status="complete", payment_status="paid"
        )
        self.assertEqual(check_payment_sessions.run()["paid"], 1)
        check_payment_sessions.run()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "PAID")
        self.retrieve.assert_called_once()

    @override_settings(STRIPE_SECRET_KEY="")
    def test_checker_without_credentials_does_not_call_stripe(self):
        self.pending()
        self.assertEqual(
            check_payment_sessions.run(), {"expired": 0, "paid": 0, "failed": 0}
        )
        self.retrieve.assert_not_called()

    def test_concurrent_same_key_renewals_share_generation(self):
        barrier = Barrier(2)
        key = uuid4()

        def worker():
            try:
                payment = Payment.objects.get(pk=self.payment.pk)
                barrier.wait(timeout=10)
                return renew_checkout(payment, key).session_id
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker) for _ in range(2)]
            self.assertEqual(
                [f.result(timeout=20) for f in futures], ["cs_test_new", "cs_test_new"]
            )
        self.assertEqual(CheckoutSession.objects.filter(request_key=key).count(), 1)
        self.assertEqual(
            {call.kwargs["idempotency_key"] for call in self.create.call_args_list},
            {f"rental-checkout-{key}"},
        )
        self.assert_single_reservation()
