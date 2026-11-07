from rest_framework import generics, permissions
from drf_spectacular.utils import extend_schema, extend_schema_view

from users.serializers import UserSerializer


@extend_schema_view(
    post=extend_schema(
        tags=["Users"],
        summary="Register using email and password",
        auth=[],
        description="Public registration. Optional first_name and last_name. Django password validators apply. Password is write-only and hashed via create_user. Privilege fields are ignored. Returns 400 for duplicate email or an invalid password.",
    )
)
class CreateUserView(generics.CreateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.AllowAny]


@extend_schema_view(
    get=extend_schema(
        tags=["Users"],
        summary="Get your profile",
        description="JWT required (401 otherwise). Always returns request.user; no user ID is accepted.",
    ),
    put=extend_schema(
        tags=["Users"],
        summary="Replace your profile",
        description="JWT required. Validates and hashes passwords with set_password; privilege fields cannot be changed. Invalid email/password returns 400.",
    ),
    patch=extend_schema(
        tags=["Users"],
        summary="Update your profile",
        description="JWT required. Password is optional, write-only, validated and hashed with set_password. Privilege fields cannot be changed. Invalid input returns 400.",
    ),
)
class ManageUserView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user
