import uuid

from django.db import models


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        PAID = "PAID"
        EXPIRED = "EXPIRED"

    class Type(models.TextChoices):
        PAYMENT = "PAYMENT"
        FINE = "FINE"

    status = models.CharField(max_length=7, choices=Status, default=Status.PENDING)
    type = models.CharField(max_length=7, choices=Type, default=Type.PAYMENT)
    borrowing = models.ForeignKey(
        "borrowings.Borrowing", on_delete=models.PROTECT, related_name="payments"
    )
    session_url = models.URLField(max_length=2048, blank=True)
    session_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    money_to_pay = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="usd", editable=False)
    request_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    checkout_parameters = models.JSONField(default=dict, editable=False)
    renewal_key = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    renewal_started_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(money_to_pay__gt=0), name="positive_payment_amount"
            ),
            models.UniqueConstraint(
                fields=["borrowing"],
                condition=models.Q(type="PAYMENT"),
                name="one_rental_payment_per_borrowing",
            ),
        ]


class CheckoutSession(models.Model):
    payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, related_name="checkout_sessions"
    )
    request_key = models.UUIDField(unique=True, editable=False)
    session_id = models.CharField(max_length=255, unique=True)
    session_url = models.URLField(max_length=2048)
    created_at = models.DateTimeField(auto_now_add=True)
