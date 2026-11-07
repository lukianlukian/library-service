from django.urls import path

from payments.views import CancelView, SuccessView

app_name = "payments"
urlpatterns = [
    path("success/", SuccessView.as_view(), name="success"),
    path("cancel/", CancelView.as_view(), name="cancel"),
]
