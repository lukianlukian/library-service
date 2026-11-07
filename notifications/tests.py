from datetime import timedelta
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from books.models import Book
from borrowings.models import Borrowing
from borrowings.services import borrow_book
from notifications.models import Notification
from notifications.services import enqueue_notification, schedule_event
from notifications.tasks import (
    TelegramUnavailable,
    check_overdue_borrowings,
    deliver_notification,
    dispatch_pending_notifications,
    send_notification,
)
from payments.services import verify_payment


@override_settings(TELEGRAM_BOT_TOKEN="mock-token", TELEGRAM_CHAT_IDS=["test-chat"])
class NotificationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="reader@example.test")
        self.book = Book.objects.create(
            title="Dune", author="Herbert", inventory=3, daily_fee="1.00", cover="HARD"
        )

    def borrow(self):
        return borrow_book(
            book=self.book,
            user=self.user,
            expected_return_date=timezone.localdate() + timedelta(days=3),
        )

    def test_borrowing_queues_only_after_commit_and_replay_does_not_duplicate(self):
        with patch("notifications.tasks.send_notification.apply_async") as queue:
            with self.captureOnCommitCallbacks(execute=True):
                borrowing = self.borrow()
                queue.assert_not_called()
            queue.assert_called_once()
            payment = borrowing.payments.get()
            with self.captureOnCommitCallbacks(execute=True):
                borrow_book(
                    book=self.book,
                    user=self.user,
                    expected_return_date=borrowing.expected_return_date,
                    request_key=payment.request_key,
                )
            self.assertEqual(queue.call_count, 1)
            self.assertEqual(Notification.objects.count(), 1)

    def test_rollback_creates_no_event_or_task(self):
        with patch("notifications.tasks.send_notification.apply_async") as queue:
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(RuntimeError):
                    with transaction.atomic():
                        self.borrow()
                        raise RuntimeError("rollback")
            queue.assert_not_called()
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(Borrowing.objects.exists())
        self.book.refresh_from_db()
        self.assertEqual(self.book.inventory, 3)

    def test_queue_outage_does_not_break_borrowing(self):
        with patch(
            "notifications.tasks.send_notification.apply_async",
            side_effect=RuntimeError,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                borrowing = self.borrow()
        self.assertTrue(Borrowing.objects.filter(pk=borrowing.pk).exists())
        self.assertIsNone(Notification.objects.get().sent_at)
        with patch("notifications.tasks.enqueue_notification") as queue:
            dispatch_pending_notifications.run()
        queue.assert_called_once_with(Notification.objects.get().pk)
        self.assertEqual(Notification.objects.count(), 1)

    def test_payment_notification_is_created_once(self):
        with patch("notifications.tasks.send_notification.apply_async") as queue:
            borrowing = self.borrow()
            payment = borrowing.payments.get()
            payment.session_id = "cs_test_mock"
            payment.save()
            session = {
                "id": payment.session_id,
                "livemode": False,
                "mode": "payment",
                "client_reference_id": str(payment.pk),
                "metadata": {"payment_id": str(payment.pk)},
                "amount_total": 300,
                "currency": "usd",
                "payment_status": "paid",
                "status": "complete",
            }
            with override_settings(STRIPE_SECRET_KEY="sk_test_mock"):
                with patch(
                    "payments.services.stripe.checkout.Session.retrieve",
                    return_value=session,
                ):
                    with self.captureOnCommitCallbacks(execute=True):
                        verify_payment(payment)
                        verify_payment(payment)
            self.assertEqual(
                Notification.objects.filter(event_key=f"payment:{payment.pk}").count(),
                1,
            )
            self.assertEqual(queue.call_count, 1)

    def test_delivery_and_duplicate_task(self):
        event = Notification.objects.create(
            event_key="example", chat_id="test-chat", message="Test"
        )
        with patch("notifications.tasks.requests.post") as post:
            post.return_value.json.return_value = {"ok": True}
            deliver_notification(event.pk)
            deliver_notification(event.pk)
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["timeout"], 5)
        event.refresh_from_db()
        self.assertIsNotNone(event.sent_at)
        self.assertEqual(event.attempts, 1)

    def test_network_failures_are_bounded_and_do_not_log_token(self):
        event = Notification.objects.create(
            event_key="example", chat_id="test-chat", message="Test"
        )
        with patch(
            "notifications.tasks.requests.post",
            side_effect=requests.Timeout("secret-url"),
        ) as post:
            for _ in range(4):
                with self.assertRaisesRegex(
                    TelegramUnavailable, "Telegram delivery failed"
                ):
                    deliver_notification(event.pk)
            deliver_notification(event.pk)
        self.assertEqual(post.call_count, 4)
        event.refresh_from_db()
        self.assertEqual(event.attempts, 4)
        self.assertIsNone(event.sent_at)

    def test_telegram_api_error_is_retryable(self):
        event = Notification.objects.create(
            event_key="example", chat_id="test-chat", message="Test"
        )
        with patch("notifications.tasks.requests.post") as post:
            post.return_value.json.return_value = {"ok": False}
            with self.assertRaises(TelegramUnavailable):
                deliver_notification(event.pk)
        event.refresh_from_db()
        self.assertIsNone(event.locked_until)

    def test_task_requests_limited_retry(self):
        with patch(
            "notifications.tasks.deliver_notification", side_effect=TelegramUnavailable
        ):
            with patch.object(
                send_notification, "retry", side_effect=TelegramUnavailable
            ) as retry:
                with self.assertRaises(TelegramUnavailable):
                    send_notification.run(1)
        retry.assert_called_once()
        self.assertEqual(send_notification.max_retries, 3)

    def test_daily_overdue_check_ignores_returned_and_not_yet_due(self):
        today = timezone.localdate()
        overdue = Borrowing.objects.create(
            book=self.book,
            user=self.user,
            expected_return_date=today - timedelta(days=1),
        )
        Borrowing.objects.create(
            book=self.book, user=self.user, expected_return_date=today
        )
        Borrowing.objects.create(
            book=self.book,
            user=self.user,
            expected_return_date=today - timedelta(days=2),
            actual_return_date=today,
        )
        with patch("notifications.tasks.send_notification.apply_async") as queue:
            with self.captureOnCommitCallbacks(execute=True):
                check_overdue_borrowings.run()
                check_overdue_borrowings.run()
        queue.assert_called_once()
        event = Notification.objects.get()
        self.assertIn(f"#{overdue.pk}", event.message)
        self.assertIn("Dune", event.message)

    @override_settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_IDS=[])
    def test_missing_configuration_disables_notifications(self):
        with patch("notifications.tasks.send_notification.apply_async") as queue:
            schedule_event("disabled", "Test")
        queue.assert_not_called()
        self.assertFalse(Notification.objects.exists())

    def test_pending_dispatch_skips_delivered_and_exhausted_events(self):
        ready = Notification.objects.create(
            event_key="ready", chat_id="test-chat", message="Test"
        )
        Notification.objects.create(
            event_key="done",
            chat_id="test-chat",
            message="Test",
            sent_at=timezone.now(),
        )
        Notification.objects.create(
            event_key="failed", chat_id="test-chat", message="Test", attempts=4
        )
        with patch("notifications.tasks.enqueue_notification") as queue:
            dispatch_pending_notifications.run()
        queue.assert_called_once_with(ready.pk)

    def test_enqueue_outage_is_swallowed(self):
        with patch(
            "notifications.tasks.send_notification.apply_async",
            side_effect=RuntimeError,
        ):
            enqueue_notification(123)
