from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from books.models import Book


class BookAPITests(APITestCase):
    def setUp(self):
        self.payload = {
            "title": "Dune",
            "author": "Frank Herbert",
            "inventory": 3,
            "daily_fee": "1.50",
            "cover": "HARD",
        }
        self.book = Book.objects.create(**self.payload)
        self.list_url = reverse("book-list")
        self.detail_url = reverse("book-detail", args=[self.book.pk])
        self.user = get_user_model().objects.create_user(email="user@example.com")
        self.staff = get_user_model().objects.create_user(
            email="staff@example.com", is_staff=True
        )

    def test_guest_can_read(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["id"], self.book.pk)
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["title"], self.book.title)
        for url in (self.list_url, self.detail_url):
            for method in ("head", "options"):
                with self.subTest(url=url, method=method):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 200)

    def test_guest_and_regular_user_cannot_write(self):
        for user in (None, self.user):
            self.client.force_authenticate(user=user)
            for method, url in (
                ("post", self.list_url),
                ("put", self.detail_url),
                ("patch", self.detail_url),
                ("delete", self.detail_url),
            ):
                with self.subTest(user=user, method=method):
                    before = list(Book.objects.values())
                    response = getattr(self.client, method)(
                        url, {**self.payload, "title": "Changed"}, format="json"
                    )
                    self.assertEqual(response.status_code, 401 if user is None else 403)
                    self.assertEqual(list(Book.objects.values()), before)

    def test_staff_can_create_update_and_delete(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(self.list_url, self.payload, format="json")
        self.assertEqual(response.status_code, 201)
        created = Book.objects.get(pk=response.data["id"])
        self.assertEqual(Book.objects.count(), 2)
        self.assertEqual(created.title, self.payload["title"])
        url = reverse("book-detail", args=[created.pk])
        updated = {**self.payload, "title": "Dune Messiah", "inventory": 5}
        response = self.client.put(url, updated, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["title"], updated["title"])
        created.refresh_from_db()
        self.assertEqual(created.title, updated["title"])
        self.assertEqual(created.inventory, 5)
        response = self.client.patch(url, {"inventory": 0}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["inventory"], 0)
        created.refresh_from_db()
        self.assertEqual(created.inventory, 0)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Book.objects.filter(pk=created.pk).exists())
        self.assertEqual(Book.objects.count(), 1)

    def test_invalid_values_are_rejected_on_create_and_update(self):
        self.client.force_authenticate(user=self.staff)
        for field, value in (
            ("inventory", -1),
            ("daily_fee", "-0.01"),
            ("cover", "UNKNOWN"),
        ):
            for method, url in (
                ("post", self.list_url),
                ("put", self.detail_url),
                ("patch", self.detail_url),
            ):
                with self.subTest(field=field, method=method):
                    before = list(Book.objects.values())
                    response = getattr(self.client, method)(
                        url, {**self.payload, field: value}, format="json"
                    )
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(field, response.data)
                    self.assertEqual(list(Book.objects.values()), before)

    def test_staff_can_create_with_zero_inventory(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(
            self.list_url, {**self.payload, "inventory": 0}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["inventory"], 0)
        self.assertEqual(Book.objects.get(pk=response.data["id"]).inventory, 0)
        self.assertEqual(Book.objects.count(), 2)
