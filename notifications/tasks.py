from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from borrowings.models import Borrowing
from notifications.models import Notification
from notifications.services import enqueue_notification, schedule_event


class TelegramUnavailable(Exception):
    pass


def deliver_notification(notification_id):
    now = timezone.now()
    claimed = Notification.objects.filter(
        Q(locked_until__isnull=True) | Q(locked_until__lt=now),
        pk=notification_id,
        sent_at__isnull=True,
        attempts__lt=4,
    ).update(attempts=F("attempts") + 1, locked_until=now + timedelta(seconds=60))
    if not claimed:
        return
    event = Notification.objects.get(pk=notification_id)
    try:
        if not settings.TELEGRAM_BOT_TOKEN:
            raise TelegramUnavailable("Telegram configuration missing.")
        response = requests.post(
            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": event.chat_id, "text": event.message},
            timeout=5,
        )
        response.raise_for_status()
        if not response.json().get("ok"):
            raise TelegramUnavailable("Telegram rejected notification.")
    except requests.RequestException, ValueError, TelegramUnavailable:
        Notification.objects.filter(pk=event.pk).update(locked_until=None)
        raise TelegramUnavailable("Telegram delivery failed.") from None
    Notification.objects.filter(pk=event.pk).update(
        sent_at=timezone.now(), locked_until=None
    )


@shared_task(bind=True, max_retries=3, ignore_result=True)
def send_notification(self, notification_id):
    try:
        deliver_notification(notification_id)
    except TelegramUnavailable as error:
        raise self.retry(exc=error, countdown=min(5 * 2**self.request.retries, 60))


@shared_task(ignore_result=True)
def dispatch_pending_notifications():
    ids = Notification.objects.filter(
        Q(locked_until__isnull=True) | Q(locked_until__lt=timezone.now()),
        sent_at__isnull=True,
        attempts__lt=4,
    ).values_list("pk", flat=True)
    for notification_id in ids.iterator():
        enqueue_notification(notification_id)


@shared_task(ignore_result=True)
def check_overdue_borrowings():
    today = timezone.localdate()
    borrowings = Borrowing.objects.filter(
        actual_return_date__isnull=True, expected_return_date__lt=today
    ).select_related("book")
    for borrowing in borrowings.iterator():
        with transaction.atomic():
            schedule_event(
                f"overdue:{today}:{borrowing.pk}",
                f"Overdue borrowing #{borrowing.pk}: {borrowing.book.title}; "
                f"user #{borrowing.user_id}; due {borrowing.expected_return_date}.",
            )
