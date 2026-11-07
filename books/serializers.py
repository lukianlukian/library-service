from rest_framework import serializers

from books.models import Book


class BookSerializer(serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ("id", "title", "author", "inventory", "daily_fee", "cover")
        extra_kwargs = {
            "inventory": {"min_value": 0},
            "daily_fee": {"min_value": 0},
        }
