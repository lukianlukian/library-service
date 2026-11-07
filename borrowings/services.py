import time

from django.db import OperationalError, connection, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from books.models import Book
from borrowings.models import Borrowing


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


def borrow_book(*, book, user, expected_return_date):
    def operation():
        # The first statement in the transaction is a conditional write, not
        # a read followed by a lock upgrade. This also works on SQLite.
        changed = Book.objects.filter(pk=book.pk, inventory__gt=0).update(
            inventory=F("inventory") - 1
        )
        if not changed:
            raise ValidationError({"book": "No copies available."})
        return Borrowing.objects.create(
            book=book, user=user, expected_return_date=expected_return_date
        )

    return _atomic_write(operation)


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
