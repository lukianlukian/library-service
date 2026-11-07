from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import stripe
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from books.models import Book
from borrowings.models import Borrowing
from payments.models import Payment


@override_settings(
    STRIPE_SECRET_KEY="sk_test_mock", STRIPE_PUBLIC_BASE_URL="http://localhost:8000"
)
class PaymentAPITests(APITransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="reader@example.test")
        self.book = Book.objects.create(
            title="Dune", author="Herbert", inventory=2, daily_fee="1.25", cover="HARD"
        )
        self.key = str(uuid4())
        self.client.force_authenticate(self.user)
        self.client.credentials(HTTP_IDEMPOTENCY_KEY=self.key)
        self.url = reverse("borrowings:borrowing-list")
        self.payload = {
            "book": self.book.pk,
            "expected_return_date": str(timezone.localdate() + timedelta(days=3)),
        }
        self.session = {
            "id": "cs_test_mock",
            "url": "https://checkout.stripe.com/test",
            "livemode": False,
        }
        self.create_mock = patch(
            "payments.services.stripe.checkout.Session.create",
            return_value=self.session,
        ).start()
        self.retrieve_mock = patch(
            "payments.services.stripe.checkout.Session.retrieve"
        ).start()
        self.addCleanup(patch.stopall)

    def create_payment(self):
        response = self.client.post(self.url, self.payload)
        self.assertEqual(response.status_code, 201, response.data)
        return Payment.objects.get()

    def paid_session(self, payment, **overrides):
        return {
            **self.session,
            "payment_status": "paid",
            "status": "complete",
            "mode": "payment",
            "amount_total": 375,
            "currency": "usd",
            "client_reference_id": str(payment.pk),
            "metadata": {"payment_id": str(payment.pk)},
            **overrides,
        }

    def callback(self, name="success", session_id="cs_test_mock"):
        self.client.force_authenticate(None)
        return self.client.get(reverse(f"payments:{name}"), {"session_id": session_id})

    def test_create_session_amount_and_no_database_lock_during_network(self):
        def network(**kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertTrue(Borrowing.objects.exists())
            self.assertTrue(Payment.objects.exists())
            return self.session

        self.create_mock.side_effect = network
        payment = self.create_payment()
        self.assertEqual(payment.status, "PENDING")
        self.assertEqual(payment.type, "PAYMENT")
        self.assertEqual(payment.money_to_pay, Decimal("3.75"))
        self.assertEqual(payment.session_id, "cs_test_mock")
        self.assertEqual(payment.session_url, self.session["url"])
        params = self.create_mock.call_args.kwargs
        self.assertEqual(params["line_items"][0]["price_data"]["unit_amount"], 375)
        self.assertEqual(params["line_items"][0]["price_data"]["currency"], "usd")
        self.assertEqual(params["idempotency_key"], f"rental-checkout-{self.key}")
        self.assertIn("{CHECKOUT_SESSION_ID}", params["success_url"])
        self.book.refresh_from_db()
        self.assertEqual(self.book.inventory, 1)

    def test_same_day_rental_charges_one_day(self):
        self.payload["expected_return_date"] = str(timezone.localdate())
        payment = self.create_payment()
        self.assertEqual(payment.money_to_pay, Decimal("1.25"))

    def test_nonpositive_and_below_minimum_totals_do_not_reserve_inventory(self):
        for fee in ("0.00", "0.01"):
            self.book.daily_fee = fee
            self.book.save()
            response = self.client.post(self.url, self.payload)
            self.assertEqual(response.status_code, 400)
            self.assertFalse(Borrowing.objects.exists())
            self.assertFalse(Payment.objects.exists())
            self.book.refresh_from_db()
            self.assertEqual(self.book.inventory, 2)
        self.create_mock.assert_not_called()

    def test_confirmed_payment_and_repeat_success(self):
        payment = self.create_payment()
        self.retrieve_mock.return_value = self.paid_session(payment)
        response = self.callback()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["payment"]["status"], "PAID")
        payment.refresh_from_db()
        self.assertEqual(payment.status, "PAID")
        self.assertEqual(self.callback().status_code, 200)
        self.retrieve_mock.assert_called_once()
        self.assertEqual(Payment.objects.count(), 1)
        self.book.refresh_from_db()
        self.assertEqual(self.book.inventory, 1)

    def test_unpaid_incomplete_and_mismatched_sessions_stay_pending(self):
        payment = self.create_payment()
        for changes in (
            {"payment_status": "unpaid"},
            {"status": "open"},
            {"amount_total": 374},
            {"currency": "eur"},
            {"livemode": True},
            {"client_reference_id": "other"},
            {"metadata": {}},
            {"id": "cs_test_other"},
            {"mode": "subscription"},
        ):
            with self.subTest(changes=changes):
                self.retrieve_mock.return_value = self.paid_session(payment, **changes)
                self.assertEqual(self.callback().status_code, 400)
                payment.refresh_from_db()
                self.assertEqual(payment.status, "PENDING")

    def test_cancel_preserves_pending_and_returns_resume_url(self):
        payment = self.create_payment()
        response = self.callback("cancel")
        self.assertEqual(response.status_code, 200)
        self.assertIn("continue payment", response.data["detail"])
        self.assertEqual(response.data["payment"]["session_url"], self.session["url"])
        payment.refresh_from_db()
        self.assertEqual(payment.status, "PENDING")
        self.retrieve_mock.assert_not_called()
        self.book.refresh_from_db()
        self.assertEqual(self.book.inventory, 1)

    def test_cancel_never_downgrades_paid_payment(self):
        payment = self.create_payment()
        payment.status = "PAID"
        payment.save()
        self.assertEqual(self.callback("cancel").data["payment"]["status"], "PAID")
        payment.refresh_from_db()
        self.assertEqual(payment.status, "PAID")

    def test_stripe_failure_can_be_retried_without_duplicate_reservation(self):
        self.create_mock.side_effect = stripe.APIConnectionError("test network failure")
        response = self.client.post(self.url, self.payload)
        self.assertEqual(response.status_code, 503)
        payment = Payment.objects.get()
        self.assertEqual(response.data["payment_id"], payment.pk)
        self.assertIsNone(payment.session_id)
        self.assertEqual(payment.status, "PENDING")
        original_params = dict(self.create_mock.call_args.kwargs)
        # Retrying must use the original price/URLs even after settings/book changes.
        self.book.daily_fee = "9.99"
        self.book.save(update_fields=["daily_fee"])
        self.create_mock.side_effect = None
        with override_settings(STRIPE_PUBLIC_BASE_URL="https://changed.example.test"):
            response = self.client.post(self.url, self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.create_mock.call_args.kwargs, original_params)
        self.assertEqual(Borrowing.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.book.refresh_from_db()
        self.assertEqual(self.book.inventory, 1)
        payment.refresh_from_db()
        self.assertEqual(payment.session_id, self.session["id"])
        self.assertEqual(payment.money_to_pay, Decimal("3.75"))

    def test_replay_of_successful_creation_never_calls_stripe_again(self):
        payment = self.create_payment()
        response = self.client.post(self.url, self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], payment.borrowing_id)
        self.create_mock.assert_called_once()
        self.assertEqual(Borrowing.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.book.refresh_from_db()
        self.assertEqual(self.book.inventory, 1)

    def test_session_created_but_database_save_failed_is_recovered(self):
        with patch(
            "payments.services.Payment.objects.filter", side_effect=RuntimeError
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self.payload)
        self.assertEqual(Payment.objects.count(), 1)
        original_params = dict(self.create_mock.call_args.kwargs)
        response = self.client.post(self.url, self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.create_mock.call_args.kwargs, original_params)
        self.assertEqual(Borrowing.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.book.refresh_from_db()
        self.assertEqual(self.book.inventory, 1)

    def test_key_reuse_with_other_payload_or_user_is_rejected(self):
        self.create_payment()
        response = self.client.post(
            self.url, {**self.payload, "expected_return_date": "2030-01-01"}
        )
        self.assertEqual(response.status_code, 409)
        other = get_user_model().objects.create_user(email="other@example.test")
        self.client.force_authenticate(other)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 409)
        self.assertEqual(Borrowing.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)

    def test_missing_idempotency_key_is_rejected(self):
        self.client.credentials()
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 400)
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(Borrowing.objects.exists())

    def test_unknown_outcome_is_not_retried_after_stripe_key_expiry(self):
        self.create_mock.side_effect = stripe.APIConnectionError("timeout")
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 503)
        Payment.objects.update(created_at=timezone.now() - timedelta(hours=24))
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 409)
        self.create_mock.assert_called_once()
        self.assertEqual(Borrowing.objects.count(), 1)

    def test_invalid_and_live_keys_never_reach_stripe(self):
        for key in ("", "sk_live_refused"):
            with override_settings(STRIPE_SECRET_KEY=key):
                self.assertEqual(
                    self.client.post(self.url, self.payload).status_code, 503
                )
        self.create_mock.assert_not_called()
        self.assertEqual(Payment.objects.count(), 1)

    def test_live_session_from_provider_is_rejected(self):
        self.create_mock.return_value = {**self.session, "livemode": True}
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 503)
        self.assertEqual(Payment.objects.get().status, "PENDING")
        self.assertIsNone(Payment.objects.get().session_id)

    def test_verification_network_failure_does_not_mark_paid(self):
        payment = self.create_payment()
        self.retrieve_mock.side_effect = stripe.APIConnectionError("timeout")
        self.assertEqual(self.callback().status_code, 503)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "PENDING")

    def test_missing_and_unknown_session_ids(self):
        self.client.force_authenticate(None)
        for name in ("success", "cancel"):
            self.assertEqual(
                self.client.get(reverse(f"payments:{name}")).status_code, 400
            )
            self.assertEqual(self.callback(name, "cs_test_missing").status_code, 404)

    def test_payment_fields_are_server_managed(self):
        response = self.client.post(
            self.url,
            {
                **self.payload,
                "payments": [{"status": "PAID", "money_to_pay": "0.01"}],
                "money_to_pay": "0.01",
                "session_id": "fake",
                "status": "PAID",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        payment = Payment.objects.get()
        self.assertEqual(payment.status, "PENDING")
        self.assertEqual(payment.money_to_pay, Decimal("3.75"))
