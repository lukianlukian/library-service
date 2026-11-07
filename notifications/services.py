import logging

from django.conf import settings
from django.db import transaction

from notifications.models import Notification

logger = logging.getLogger(__name__)


def enqueue_notification(notification_id):
    from notifications.tasks import send_notification

    try:
        send_notification.apply_async(args=[notification_id], retry=False)
    except Exception:
        # Keep the outbox row for the periodic dispatcher. Do not log broker
        # URLs/credentials or let a queue outage fail a committed API request.
        logger.warning("Notification queue unavailable; event retained in outbox.")


def schedule_event(event_key, message):
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_IDS:
        return
    for chat_id in settings.TELEGRAM_CHAT_IDS:
        event, created = Notification.objects.get_or_create(
            event_key=event_key, chat_id=chat_id, defaults={"message": message}
        )
        if created:
            transaction.on_commit(
                lambda pk=event.pk: enqueue_notification(pk), robust=True
            )
