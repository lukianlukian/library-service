from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from drf_spectacular.utils import extend_schema

from users.views import CreateUserView, ManageUserView

app_name = "users"

urlpatterns = [
    path("", CreateUserView.as_view(), name="create"),
    path(
        "token/",
        extend_schema(
            tags=["Authentication"],
            summary="Obtain access and refresh tokens",
            auth=[],
            description="Public endpoint. Submit email and password. Invalid credentials return 401; missing fields return 400. Use the access token in Swagger Authorize, without the Bearer prefix.",
        )(TokenObtainPairView).as_view(),
        name="token",
    ),
    path(
        "token/refresh/",
        extend_schema(
            tags=["Authentication"],
            summary="Refresh an access token",
            auth=[],
            description="Public endpoint. Submit refresh token; returns access token. Invalid or expired tokens return 401, missing refresh returns 400.",
        )(TokenRefreshView).as_view(),
        name="token-refresh",
    ),
    path("me/", ManageUserView.as_view(), name="me"),
]
