from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from books.models import Book
from borrowings.models import Borrowing
from payments.models import Payment


@override_settings(STRIPE_SECRET_KEY="sk_test_mock")
class PendingPaymentRestrictionTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="reader@example.test")
        self.book = Book.objects.create(
            title="Dune", author="Herbert", inventory=3, daily_fee="1.00", cover="HARD"
        )
        self.client.force_authenticate(self.user)
        self.key = str(uuid4())
        self.client.credentials(HTTP_IDEMPOTENCY_KEY=self.key)
        self.payload = {
            "book": self.book.pk,
            "expected_return_date": str(timezone.localdate() + timedelta(days=3)),
        }
        self.url = reverse("borrowings:borrowing-list")
        self.stripe = patch(
            "payments.services.stripe.checkout.Session.create",
            return_value={
                "id": "cs_test_mock",
                "url": "https://checkout.stripe.com/test",
                "livemode": False,
            },
        ).start()
        self.addCleanup(patch.stopall)

    def debt(self, user=None, status="PENDING", type="PAYMENT"):
        borrowing = Borrowing.objects.create(
            book=self.book,
            user=user or self.user,
            expected_return_date=timezone.localdate(),
        )
        return Payment.objects.create(
            borrowing=borrowing, money_to_pay="1.00", status=status, type=type
        )

    def test_pending_rental_and_fine_block_new_borrowing_without_changes(self):
        payment = self.debt()
        for kind in ("PAYMENT", "FINE"):
            payment.type = kind
            payment.save()
            before = list(Payment.objects.values()), list(Borrowing.objects.values())
            response = self.client.post(self.url, self.payload)
            self.assertEqual(response.status_code, 400)
            self.assertIn("payment", response.data)
            self.assertEqual(
                (list(Payment.objects.values()), list(Borrowing.objects.values())),
                before,
            )
            self.book.refresh_from_db()
            self.assertEqual(self.book.inventory, 3)
        self.stripe.assert_not_called()

    def test_paid_debt_does_not_block(self):
        self.debt(status="PAID")
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 201)
        self.book.refresh_from_db()
        self.assertEqual(self.book.inventory, 2)

    def test_other_users_debt_does_not_block(self):
        other = get_user_model().objects.create_user(email="other@example.test")
        self.debt(user=other)
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 201)
        self.assertEqual(Borrowing.objects.filter(user=self.user).count(), 1)

    def test_pending_checkout_retry_does_not_create_second_borrowing(self):
        first = self.client.post(self.url, self.payload)
        self.assertEqual(first.status_code, 201)
        replay = self.client.post(self.url, self.payload)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data["id"], first.data["id"])
        self.client.credentials(HTTP_IDEMPOTENCY_KEY=str(uuid4()))
        self.assertEqual(self.client.post(self.url, self.payload).status_code, 400)
        self.assertEqual(Borrowing.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.book.refresh_from_db()
        self.assertEqual(self.book.inventory, 2)
