import time
import uuid

from django.db import IntegrityError, OperationalError, connection, transaction
from django.contrib.auth import get_user_model
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from books.models import Book
from borrowings.models import Borrowing
from payments.models import Payment
from payments.services import IdempotencyConflict, checkout_parameters, rental_amount
from notifications.services import schedule_event


def _atomic_write(operation):
    # SQLite serializes writers. Retry a busy transaction from its beginning,
    # including on shared-cache SQLite databases used by Django's tests.
    for attempt in range(6):
        try:
            with transaction.atomic():
                return operation()
        except OperationalError as error:
            code = getattr(error.__cause__, "sqlite_errorcode", 0)
            if (
                connection.vendor != "sqlite"
                or code & 0xFF not in (5, 6)
                or attempt == 5
            ):
                raise
            time.sleep(0.01 * 2**attempt)


def borrow_book(*, book, user, expected_return_date, request_key=None):
    request_key = request_key or uuid.uuid4()

    def existing_borrowing():
        payment = (
            Payment.objects.select_related("borrowing")
            .filter(request_key=request_key)
            .first()
        )
        if payment is None:
            return None
        borrowing = payment.borrowing
        if (
            borrowing.user_id != user.pk
            or borrowing.book_id != book.pk
            or borrowing.expected_return_date != expected_return_date
        ):
            raise IdempotencyConflict()
        return borrowing

    existing = _atomic_write(existing_borrowing)
    if existing:
        return existing

    def operation():
        # Serialize new rentals for a user before checking debt. This is a
        # write-first transaction on SQLite and a row lock on PostgreSQL.
        get_user_model().objects.filter(pk=user.pk).update(last_login=F("last_login"))
        replay = existing_borrowing()
        if replay:
            return replay
        if Payment.objects.filter(
            borrowing__user=user, status=Payment.Status.PENDING
        ).exists():
            raise ValidationError(
                {
                    "payment": "Outstanding PENDING payments must be paid before borrowing another book."
                }
            )
        # Conditional decrement prevents issuing an unavailable copy.
        changed = Book.objects.filter(pk=book.pk, inventory__gt=0).update(
            inventory=F("inventory") - 1
        )
        if not changed:
            raise ValidationError({"book": "No copies available."})
        current_book = Book.objects.get(pk=book.pk)
        today = timezone.localdate()
        amount = rental_amount(
            current_book.daily_fee, (expected_return_date - today).days
        )
        borrowing = Borrowing.objects.create(
            book=book, user=user, expected_return_date=expected_return_date
        )
        payment = Payment.objects.create(
            borrowing=borrowing, money_to_pay=amount, request_key=request_key
        )
        payment.checkout_parameters = checkout_parameters(payment)
        payment.save(update_fields=["checkout_parameters"])
        schedule_event(
            f"borrowing:{borrowing.pk}",
            f"New borrowing #{borrowing.pk}: {current_book.title}; user #{user.pk}; due {expected_return_date}.",
        )
        return borrowing

    try:
        return _atomic_write(operation)
    except IntegrityError:
        # A concurrent request may have won the unique request_key insert.
        # Its competitor's inventory decrement is rolled back with the insert.
        existing = _atomic_write(existing_borrowing)
        if existing:
            return existing
        raise


def return_borrowing(*, borrowing):
    def operation():
        changed = Borrowing.objects.filter(
            pk=borrowing.pk, actual_return_date__isnull=True
        ).update(actual_return_date=timezone.localdate())
        if not changed:
            raise ValidationError(
                {"detail": "This borrowing has already been returned."}
            )
        Book.objects.filter(pk=borrowing.book_id).update(inventory=F("inventory") + 1)
        borrowing.refresh_from_db()
        return borrowing

    return _atomic_write(operation)
