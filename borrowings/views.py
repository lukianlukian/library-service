from rest_framework import mixins, permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)

from borrowings.models import Borrowing
from borrowings.serializers import BorrowingSerializer
from borrowings.services import return_borrowing


@extend_schema_view(
    list=extend_schema(
        tags=["Borrowings"],
        summary="List your borrowings (staff: all)",
        description="JWT required. Regular users only see their own records; user_id is ignored for them. Staff can filter all records by user_id. Active means actual_return_date is null.",
        parameters=[
            OpenApiParameter(
                "is_active",
                str,
                enum=["true", "false"],
                description="true: not returned; false: returned.",
            ),
            OpenApiParameter(
                "user_id",
                int,
                description="Positive user ID. Staff only; ignored for regular users.",
            ),
        ],
        responses={
            200: BorrowingSerializer(many=True),
            400: OpenApiResponse(description="Invalid filter value."),
            401: OpenApiResponse(description="Missing or invalid JWT."),
        },
    ),
    retrieve=extend_schema(
        tags=["Borrowings"],
        summary="Get a borrowing",
        description="Owner or staff only. Other users receive 404 to avoid disclosing records.",
        responses={
            200: BorrowingSerializer,
            401: OpenApiResponse(description="JWT required."),
            404: OpenApiResponse(description="Not found or owned by another user."),
        },
    ),
    create=extend_schema(
        tags=["Borrowings"],
        summary="Borrow an available book",
        description="JWT required. Submit only book (ID) and expected_return_date (YYYY-MM-DD, today or later). user comes from request.user; id, user, borrow_date and actual_return_date are read-only. Creation and inventory decrement are atomic, including concurrent requests on SQLite. No PUT/PATCH/DELETE operations are allowed.",
        responses={
            201: BorrowingSerializer,
            400: OpenApiResponse(
                description="Book unavailable/not found, invalid or past expected_return_date, or missing required fields."
            ),
            401: OpenApiResponse(description="Missing or invalid JWT."),
        },
    ),
)
class BorrowingViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = BorrowingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Borrowing.objects.none()
        queryset = Borrowing.objects.all().order_by("id")
        if not self.request.user.is_staff:
            queryset = queryset.filter(user=self.request.user)
        # Query filters apply to the list, never to the return operation.
        if self.action == "list":
            active = self.request.query_params.get("is_active")
            if active is not None:
                if active not in ("true", "false"):
                    raise serializers.ValidationError(
                        {"is_active": "Use true or false."}
                    )
                queryset = queryset.filter(actual_return_date__isnull=active == "true")
            user_id = self.request.query_params.get("user_id")
            if user_id is not None and self.request.user.is_staff:
                try:
                    user_id = int(user_id)
                    if user_id < 1 or user_id > 9223372036854775807:
                        raise ValueError
                except ValueError:
                    raise serializers.ValidationError(
                        {"user_id": "Use a positive integer."}
                    )
                queryset = queryset.filter(user_id=user_id)
        return queryset

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(
        tags=["Borrowings"],
        summary="Return a borrowed book",
        request=None,
        description="Owner or staff only. No request body. Server sets actual_return_date to today and increases inventory by one in a single transaction. Conditional updates prevent concurrent double returns. Client-supplied fields are ignored.",
        responses={
            200: BorrowingSerializer,
            400: OpenApiResponse(
                description="Already returned; inventory is unchanged."
            ),
            401: OpenApiResponse(description="Missing or invalid JWT."),
            404: OpenApiResponse(description="Not found or owned by another user."),
        },
    )
    @action(detail=True, methods=["post"], url_path="return")
    def return_book(self, request, pk=None):
        borrowing = return_borrowing(borrowing=self.get_object())
        return Response(self.get_serializer(borrowing).data)
