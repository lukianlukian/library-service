from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase


class UsersAPITests(APITestCase):
    def setUp(self):
        self.payload = {
            "email": "reader@example.com",
            "password": "Strong-Reading-928!",
            "first_name": "Alice",
            "last_name": "Reader",
        }
        self.create_url = reverse("users:create")
        self.me_url = reverse("users:me")
        self.token_url = reverse("users:token")
        self.refresh_url = reverse("users:token-refresh")

    def create_user(self):
        return get_user_model().objects.create_user(**self.payload)

    def authenticate(self):
        response = self.client.post(self.token_url, self.payload)
        self.assertEqual(response.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        return response.data

    def test_registration_hashes_password_and_never_returns_it(self):
        response = self.client.post(self.create_url, self.payload)
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("password", response.data)
        user = get_user_model().objects.get(pk=response.data["id"])
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(user.email, self.payload["email"])
        self.assertEqual(user.first_name, "Alice")
        self.assertEqual(user.last_name, "Reader")
        self.assertNotEqual(user.password, self.payload["password"])
        self.assertTrue(user.check_password(self.payload["password"]))

    def test_duplicate_email_is_rejected(self):
        user = self.create_user()
        response = self.client.post(self.create_url, self.payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data)
        self.assertEqual(get_user_model().objects.count(), 1)
        user.refresh_from_db()
        self.assertTrue(user.check_password(self.payload["password"]))

    def test_invalid_passwords_are_rejected(self):
        for password in ("", "abc", "password", "123456789012", "reader@example.com"):
            with self.subTest(password=password):
                response = self.client.post(
                    self.create_url, {**self.payload, "password": password}
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("password", response.data)
                self.assertFalse(get_user_model().objects.exists())
        payload = {k: v for k, v in self.payload.items() if k != "password"}
        self.assertEqual(self.client.post(self.create_url, payload).status_code, 400)
        self.assertFalse(get_user_model().objects.exists())

    def test_registration_cannot_elevate_privileges(self):
        response = self.client.post(
            self.create_url,
            {**self.payload, "is_staff": True, "is_superuser": True},
        )
        self.assertEqual(response.status_code, 201)
        user = get_user_model().objects.get(pk=response.data["id"])
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_tokens_and_refresh_authenticate_the_user(self):
        user = self.create_user()
        tokens = self.authenticate()
        self.assertIn("refresh", tokens)
        self.assertNotIn("password", tokens)
        response = self.client.get(self.me_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], user.pk)
        self.client.credentials()
        response = self.client.post(self.refresh_url, {"refresh": tokens["refresh"]})
        self.assertEqual(response.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        self.assertEqual(self.client.get(self.me_url).data["id"], user.pk)

    def test_invalid_credentials_and_tokens_are_rejected(self):
        self.create_user()
        for payload in (
            {**self.payload, "password": "Wrong-password!"},
            {**self.payload, "email": "missing@example.com"},
        ):
            response = self.client.post(self.token_url, payload)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn("access", response.data)
        self.assertEqual(
            self.client.post(self.refresh_url, {"refresh": "invalid"}).status_code, 401
        )
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
        self.assertEqual(self.client.get(self.me_url).status_code, 401)

    def test_guest_cannot_access_or_modify_profile(self):
        user = self.create_user()
        before = list(get_user_model().objects.values())
        for method in ("get", "put", "patch"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.me_url, self.payload)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(list(get_user_model().objects.values()), before)

    def test_profile_returns_only_current_user_without_password(self):
        user = self.create_user()
        get_user_model().objects.create_user(email="other@example.com")
        self.authenticate()
        response = self.client.get(self.me_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "id": user.pk,
                "email": user.email,
                "first_name": "Alice",
                "last_name": "Reader",
            },
        )

    def test_put_and_patch_modify_only_current_user(self):
        user = self.create_user()
        other = get_user_model().objects.create_user(email="other@example.com")
        other_before = get_user_model().objects.values().get(pk=other.pk)
        self.authenticate()
        payload = {
            "email": "updated@example.com",
            "first_name": "Updated",
            "last_name": "Name",
            "password": "Changed-Password-829!",
            "is_staff": True,
            "is_superuser": True,
            "id": other.pk,
        }
        response = self.client.put(self.me_url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("password", response.data)
        user.refresh_from_db()
        self.assertEqual(user.email, payload["email"])
        self.assertEqual(user.first_name, "Updated")
        self.assertTrue(user.check_password(payload["password"]))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        response = self.client.patch(self.me_url, {"last_name": "Patched"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("password", response.data)
        user.refresh_from_db()
        self.assertEqual(user.last_name, "Patched")
        self.assertTrue(user.check_password(payload["password"]))
        self.assertEqual(
            get_user_model().objects.values().get(pk=other.pk), other_before
        )

    def test_invalid_profile_update_leaves_database_unchanged(self):
        self.create_user()
        get_user_model().objects.create_user(email="other@example.com")
        self.authenticate()
        before = list(get_user_model().objects.values())
        for payload in ({"password": "abc"}, {"email": "other@example.com"}):
            response = self.client.patch(self.me_url, payload)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(list(get_user_model().objects.values()), before)

    def test_patch_cannot_elevate_privileges(self):
        user = self.create_user()
        self.authenticate()
        response = self.client.patch(
            self.me_url, {"is_staff": True, "is_superuser": True}
        )
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
