from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase, APITransactionTestCase

from books.models import Book
from borrowings.models import Borrowing
from borrowings.services import borrow_book, return_borrowing


class BorrowingAPITests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="reader@example.com")
        self.other = get_user_model().objects.create_user(email="other@example.com")
        self.staff = get_user_model().objects.create_user(
            email="staff@example.com", is_staff=True
        )
        self.book = Book.objects.create(
            title="Dune",
            author="Frank Herbert",
            inventory=2,
            daily_fee="1.50",
            cover="HARD",
        )
        self.today = timezone.localdate()
        self.list_url = reverse("borrowings:borrowing-list")
        self.payload = {
            "book": self.book.pk,
            "expected_return_date": str(self.today + timedelta(days=7)),
        }
        self.client.force_authenticate(self.user)

    def borrowing(self, user=None, returned=False):
        return Borrowing.objects.create(
            book=self.book,
            user=user or self.user,
            expected_return_date=self.today + timedelta(days=7),
            actual_return_date=self.today if returned else None,
        )

    def detail_url(self, borrowing):
        return reverse("borrowings:borrowing-detail", args=[borrowing.pk])

    def return_url(self, borrowing):
        return reverse("borrowings:borrowing-return-book", args=[borrowing.pk])

    def snapshot(self):
        return list(Book.objects.values()), list(Borrowing.objects.values())

    def test_issue_assigns_server_fields_and_decrements_inventory(self):
        response = self.client.post(
            self.list_url,
            {
                **self.payload,
                "user": self.other.pk,
                "borrow_date": "2000-01-01",
                "actual_return_date": "2000-01-02",
            },
        )
        self.assertEqual(response.status_code, 201)
        borrowing = Borrowing.objects.get(pk=response.data["id"])
        self.assertEqual(Borrowing.objects.count(), 1)
        self.assertEqual(borrowing.user, self.user)
        self.assertEqual(borrowing.book, self.book)
        self.assertEqual(borrowing.borrow_date, self.today)
        self.assertIsNone(borrowing.actual_return_date)
        self.assertEqual(
            str(borrowing.expected_return_date), self.payload["expected_return_date"]
        )
        self.assertEqual(response.data["user"], self.user.pk)
        self.assertIsNone(response.data["actual_return_date"])
        self.book.refresh_from_db()
        self.assertEqual(self.book.inventory, 1)

    def test_zero_inventory_rejected_without_database_changes(self):
        self.book.inventory = 0
        self.book.save()
        before = self.snapshot()
        response = self.client.post(self.list_url, self.payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("book", response.data)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_dates_rejected_without_database_changes(self):
        for date in (str(self.today - timedelta(days=1)), "invalid", ""):
            with self.subTest(date=date):
                before = self.snapshot()
                response = self.client.post(
                    self.list_url, {**self.payload, "expected_return_date": date}
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("expected_return_date", response.data)
                self.assertEqual(self.snapshot(), before)

    def test_today_is_allowed(self):
        response = self.client.post(
            self.list_url, {**self.payload, "expected_return_date": str(self.today)}
        )
        self.assertEqual(response.status_code, 201)
        self.book.refresh_from_db()
        self.assertEqual(self.book.inventory, 1)

    def test_invalid_book_rejected(self):
        before = self.snapshot()
        response = self.client.post(self.list_url, {**self.payload, "book": 999999})
        self.assertEqual(response.status_code, 400)
        self.assertIn("book", response.data)
        self.assertEqual(self.snapshot(), before)

    def test_return_and_repeat_return(self):
        response = self.client.post(self.list_url, self.payload)
        borrowing = Borrowing.objects.get(pk=response.data["id"])
        response = self.client.post(
            self.return_url(borrowing),
            {
                "actual_return_date": "2000-01-01",
                "user": self.other.pk,
                "book": 999999,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["actual_return_date"], str(self.today))
        borrowing.refresh_from_db()
        self.book.refresh_from_db()
        self.assertEqual(borrowing.actual_return_date, self.today)
        self.assertEqual(borrowing.user, self.user)
        self.assertEqual(borrowing.book, self.book)
        self.assertEqual(self.book.inventory, 2)
        before = self.snapshot()
        response = self.client.post(self.return_url(borrowing))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_guest_cannot_access_endpoints(self):
        borrowing = self.borrowing()
        self.client.force_authenticate(None)
        before = self.snapshot()
        for method, url in (
            ("get", self.list_url),
            ("post", self.list_url),
            ("get", self.detail_url(borrowing)),
            ("post", self.return_url(borrowing)),
        ):
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, self.payload)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(self.snapshot(), before)

    def test_user_sees_only_own_borrowings(self):
        own = self.borrowing()
        other = self.borrowing(self.other)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data], [own.pk])
        self.assertEqual(self.client.get(self.detail_url(own)).status_code, 200)
        before = self.snapshot()
        self.assertEqual(self.client.get(self.detail_url(other)).status_code, 404)
        self.assertEqual(self.client.post(self.return_url(other)).status_code, 404)
        self.assertEqual(self.snapshot(), before)
        response = self.client.get(self.list_url, {"user_id": self.other.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data], [own.pk])

    def test_staff_sees_and_returns_other_users_borrowings(self):
        own = self.borrowing()
        other = self.borrowing(self.other)
        self.client.force_authenticate(self.staff)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["id"] for row in response.data}, {own.pk, other.pk})
        self.assertEqual(self.client.get(self.detail_url(other)).status_code, 200)
        response = self.client.post(self.return_url(other))
        self.assertEqual(response.status_code, 200)
        other.refresh_from_db()
        self.book.refresh_from_db()
        self.assertEqual(other.actual_return_date, self.today)
        self.assertEqual(self.book.inventory, 3)

    def test_filters_for_user_and_staff(self):
        active = self.borrowing()
        returned = self.borrowing(returned=True)
        other = self.borrowing(self.other)
        for user in (self.user, self.staff):
            self.client.force_authenticate(user)
            for value, expected in (
                ("true", {active.pk, other.pk} if user.is_staff else {active.pk}),
                ("false", {returned.pk}),
            ):
                response = self.client.get(self.list_url, {"is_active": value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual({row["id"] for row in response.data}, expected)
        response = self.client.get(self.list_url, {"user_id": self.other.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data], [other.pk])
        response = self.client.get(
            self.list_url,
            {
                "user_id": self.other.pk,
                "is_active": "false",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_invalid_filters_return_bad_request(self):
        self.client.force_authenticate(self.staff)
        for query in ({"is_active": "yes"}, {"user_id": "abc"}, {"user_id": "-1"}):
            self.assertEqual(self.client.get(self.list_url, query).status_code, 400)

    def test_update_and_delete_are_not_allowed(self):
        borrowing = self.borrowing()
        before = self.snapshot()
        for user in (self.user, self.staff):
            self.client.force_authenticate(user)
            for method in ("put", "patch", "delete"):
                response = getattr(self.client, method)(
                    self.detail_url(borrowing),
                    {
                        "user": self.other.pk,
                        "actual_return_date": str(self.today),
                    },
                )
                self.assertEqual(response.status_code, 405)
                self.assertEqual(self.snapshot(), before)

    def test_creation_failure_rolls_back_inventory(self):
        before = self.snapshot()
        with patch(
            "borrowings.services.Borrowing.objects.create", side_effect=RuntimeError
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(self.list_url, self.payload)
        self.assertEqual(self.snapshot(), before)

    def test_return_failure_rolls_back_return_date(self):
        borrowing = self.borrowing()
        before = self.snapshot()
        with patch("borrowings.services.Book.objects.filter", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.client.post(self.return_url(borrowing))
        self.assertEqual(self.snapshot(), before)


class BorrowingConcurrencyTests(APITransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="reader@example.com")
        self.book = Book.objects.create(
            title="Dune",
            author="Frank Herbert",
            inventory=1,
            daily_fee="1.50",
            cover="HARD",
        )

    def race(self, operation):
        barrier = Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=10)
                operation()
                return "success"
            except ValidationError:
                return "rejected"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertCountEqual(results, ["success", "rejected"])

    def test_last_copy_cannot_be_borrowed_twice(self):
        self.race(
            lambda: borrow_book(
                book=self.book,
                user=self.user,
                expected_return_date=timezone.localdate() + timedelta(days=7),
            )
        )
        self.book.refresh_from_db()
        self.assertEqual(self.book.inventory, 0)
        self.assertEqual(Borrowing.objects.count(), 1)

    def test_concurrent_returns_increment_inventory_only_once(self):
        borrowing = borrow_book(
            book=self.book,
            user=self.user,
            expected_return_date=timezone.localdate() + timedelta(days=7),
        )
        self.race(lambda: return_borrowing(borrowing=borrowing))
        self.book.refresh_from_db()
        borrowing.refresh_from_db()
        self.assertEqual(self.book.inventory, 1)
        self.assertEqual(borrowing.actual_return_date, timezone.localdate())
