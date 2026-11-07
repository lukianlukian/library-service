from django.utils import timezone
from rest_framework import serializers

from borrowings.models import Borrowing
from borrowings.services import borrow_book


class BorrowingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Borrowing
        fields = (
            "id",
            "book",
            "user",
            "borrow_date",
            "expected_return_date",
            "actual_return_date",
        )
        read_only_fields = ("id", "user", "borrow_date", "actual_return_date")

    def validate_expected_return_date(self, value):
        if value < timezone.localdate():
            raise serializers.ValidationError("Return date cannot be in the past.")
        return value

    def create(self, validated_data):
        return borrow_book(**validated_data)
