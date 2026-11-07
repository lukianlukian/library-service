from rest_framework import viewsets
from drf_spectacular.utils import extend_schema, extend_schema_view

from books.models import Book
from books.permissions import IsAdminOrReadOnly
from books.serializers import BookSerializer


@extend_schema_view(
    list=extend_schema(tags=["Books"], summary="List books", auth=[]),
    retrieve=extend_schema(tags=["Books"], summary="Get a book", auth=[]),
    create=extend_schema(
        tags=["Books"],
        summary="Create a book (staff only)",
        description="Requires a JWT for an is_staff user. Inventory and daily_fee must be non-negative; cover must be HARD or SOFT.",
    ),
    update=extend_schema(tags=["Books"], summary="Replace a book (staff only)"),
    partial_update=extend_schema(tags=["Books"], summary="Update a book (staff only)"),
    destroy=extend_schema(
        tags=["Books"],
        summary="Delete a book (staff only)",
        description="Books referenced by borrowings are protected from deletion.",
    ),
)
class BookViewSet(viewsets.ModelViewSet):
    queryset = Book.objects.all()
    serializer_class = BookSerializer
    permission_classes = [IsAdminOrReadOnly]
