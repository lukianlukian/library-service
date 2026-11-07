from rest_framework import mixins, permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from borrowings.models import Borrowing
from borrowings.serializers import BorrowingSerializer
from borrowings.services import return_borrowing


class BorrowingViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = BorrowingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
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

    @action(detail=True, methods=["post"], url_path="return")
    def return_book(self, request, pk=None):
        borrowing = return_borrowing(borrowing=self.get_object())
        return Response(self.get_serializer(borrowing).data)
