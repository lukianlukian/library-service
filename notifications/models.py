from django.db import models


class Notification(models.Model):
    event_key = models.CharField(max_length=160)
    chat_id = models.CharField(max_length=100)
    message = models.TextField()
    attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event_key", "chat_id"], name="one_notification_per_event_chat"
            )
        ]
