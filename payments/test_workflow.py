from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from books.models import Book
from notifications.models import Notification
from notifications.tasks import check_overdue_borrowings, deliver_notification
from payments.models import Payment
from payments.tasks import check_payment_sessions


@override_settings(
    STRIPE_SECRET_KEY="sk_test_mock",
    TELEGRAM_BOT_TOKEN="mock-token",
    TELEGRAM_CHAT_IDS=["test-chat"],
)
class SubmissionWorkflowTests(APITransactionTestCase):
    def setUp(self):
        self.sessions = {}
        self.keys = {}
        self.password = "Library-Secure-9472!"
        self.create_mock = patch(
            "payments.services.stripe.checkout.Session.create",
            side_effect=self.create_session,
        ).start()
        patch(
            "payments.services.stripe.checkout.Session.retrieve",
            side_effect=lambda session_id, **kwargs: deepcopy(
                self.sessions[session_id]
            ),
        ).start()
        self.queue = patch("notifications.tasks.send_notification.apply_async").start()
        self.telegram = patch("notifications.tasks.requests.post").start()
        self.telegram.return_value.json.return_value = {"ok": True}
        self.addCleanup(patch.stopall)

    def create_session(self, **params):
        key = params["idempotency_key"]
        if key in self.keys:
            return deepcopy(self.sessions[self.keys[key]])
        session_id = f"cs_test_workflow_{len(self.sessions) + 1}"
        self.keys[key] = session_id
        session = {
            "id": session_id,
            "url": f"https://checkout.stripe.com/{session_id}",
            "livemode": False,
            "mode": "payment",
            "status": "open",
            "payment_status": "unpaid",
            "client_reference_id": params["client_reference_id"],
            "metadata": params["metadata"],
            "amount_total": params["line_items"][0]["price_data"]["unit_amount"],
            "currency": "usd",
        }
        self.sessions[session_id] = session
        return deepcopy(session)

    def login(self, email):
        response = self.client.post(
            reverse("users:token"), {"email": email, "password": self.password}
        )
        self.assertEqual(response.status_code, 200, response.data)
        token = response.data["access"]
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_IDEMPOTENCY_KEY=str(uuid4())
        )
        return token

    def pay(self, payment):
        session = self.sessions[payment.session_id]
        session.update(status="complete", payment_status="paid")
        response = self.client.get(
            reverse("payments:success"), {"session_id": payment.session_id}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["payment"]["status"], "PAID")

    def deliver_all(self):
        for event in Notification.objects.filter(sent_at__isnull=True):
            deliver_notification(event.pk)
        self.assertFalse(Notification.objects.filter(sent_at__isnull=True).exists())

    def test_registration_jwt_borrow_pay_notify_return_expire_renew_and_overdue(self):
        response = self.client.post(
            reverse("users:create"),
            {
                "email": "reader@example.test",
                "password": self.password,
                "first_name": "Reader",
                "last_name": "Example",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("password", response.data)
        get_user_model().objects.create_user(
            email="staff@example.test", password=self.password, is_staff=True
        )
        self.login("staff@example.test")
        response = self.client.post(
            reverse("book-list"),
            {
                "title": "Workflow book",
                "author": "Example",
                "inventory": 2,
                "daily_fee": "1.25",
                "cover": "SOFT",
            },
        )
        self.assertEqual(response.status_code, 201)
        book_id = response.data["id"]
        reader_token = self.login("reader@example.test")
        due = timezone.localdate() + timedelta(days=3)
        payload = {"book": book_id, "expected_return_date": str(due)}
        response = self.client.post(reverse("borrowings:borrowing-list"), payload)
        self.assertEqual(response.status_code, 201, response.data)
        borrowing_id = response.data["id"]
        payment = Payment.objects.get(pk=response.data["payments"][0]["id"])
        self.assertEqual(payment.money_to_pay.as_tuple().exponent, -2)
        self.assertEqual(self.sessions[payment.session_id]["amount_total"], 375)
        self.assertEqual(
            self.client.get(
                reverse("payments:cancel"), {"session_id": payment.session_id}
            ).data["payment"]["status"],
            "PENDING",
        )
        self.pay(payment)
        self.deliver_all()
        response = self.client.post(
            reverse("borrowings:borrowing-return-book", args=[borrowing_id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Book.objects.get(pk=book_id).inventory, 2)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {reader_token}",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        response = self.client.post(reverse("borrowings:borrowing-list"), payload)
        self.assertEqual(response.status_code, 201, response.data)
        payment = Payment.objects.get(pk=response.data["payments"][0]["id"])
        self.sessions[payment.session_id].update(status="expired")
        self.assertEqual(check_payment_sessions.run()["expired"], 1)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {reader_token}",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        response = self.client.post(reverse("payments:renew", args=[payment.pk]))
        self.assertEqual(response.status_code, 200, response.data)
        payment.refresh_from_db()
        self.pay(payment)
        with patch(
            "notifications.tasks.timezone.localdate",
            return_value=due + timedelta(days=1),
        ):
            check_overdue_borrowings.run()
        self.deliver_all()
        self.assertEqual(
            Notification.objects.filter(event_key__startswith="overdue:").count(), 1
        )
        self.assertEqual(Payment.objects.filter(status="PAID").count(), 2)
        self.assertEqual(Book.objects.get(pk=book_id).inventory, 1)
        self.assertEqual(self.telegram.call_count, 5)

    def test_openapi_custom_endpoints_and_sensitive_field_contract(self):
        response = self.client.get(reverse("schema"), {"format": "json"})
        self.assertEqual(response.status_code, 200)
        schema = response.data
        paths = schema["paths"]
        self.assertIn("post", paths["/api/payments/{id}/renew/"])
        self.assertIn("get", paths["/api/payments/success/"])
        self.assertIn("get", paths["/api/payments/cancel/"])
        self.assertNotIn("requestBody", paths["/api/borrowings/{id}/return/"]["post"])
        params = {
            param["name"] for param in paths["/api/borrowings/"]["get"]["parameters"]
        }
        self.assertTrue({"user_id", "is_active"}.issubset(params))
        components = schema["components"]
        self.assertEqual(components["securitySchemes"]["jwtAuth"]["scheme"], "bearer")
        self.assertTrue(
            components["schemas"]["UserRequest"]["properties"]["password"]["writeOnly"]
        )
        self.assertNotIn("password", components["schemas"]["User"]["properties"])
        for field in ("id", "user", "borrow_date", "actual_return_date", "payments"):
            self.assertTrue(
                components["schemas"]["Borrowing"]["properties"][field]["readOnly"]
            )
