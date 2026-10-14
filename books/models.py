from django.db import models


class Book(models.Model):
    COVER_CHOICES = [
        ("HARD", "Hard"),
        ("SOFT", "Soft"),
    ]
    title = models.CharField(max_length=100)
    author = models.CharField(max_length=100)
    inventory = models.PositiveIntegerField()
    daily_fee = models.DecimalField(max_digits=10, decimal_places=2)
    cover = models.CharField(choices=COVER_CHOICES, max_length=4)

    def __str__(self):
        return self.title
