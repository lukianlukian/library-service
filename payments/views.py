from django.shortcuts import get_object_or_404
import uuid
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from payments.models import Payment
from payments.serializers import PaymentResultSerializer, PaymentSerializer
from payments.services import renew_checkout, verify_payment


def session_payment(request):
    session_id = request.query_params.get("session_id")
    if not session_id:
        raise serializers.ValidationError(
            {"session_id": "This query parameter is required."}
        )
    return get_object_or_404(Payment, session_id=session_id)


class SuccessView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=["Payments"],
        auth=[],
        summary="Verify a Stripe test payment",
        description="Public Stripe redirect. session_id is an unguessable capability, not proof of payment. Server retrieves Stripe session and verifies test mode, complete/paid status, payment identity, amount and USD currency. Only then transitions PENDING to PAID, once. No inventory changes. Already paid requests are idempotent.",
        parameters=[OpenApiParameter("session_id", str, required=True)],
        responses={
            200: PaymentResultSerializer,
            400: OpenApiResponse(
                description="Missing session_id, unpaid session or mismatched payment."
            ),
            404: OpenApiResponse(description="Unknown session."),
            503: OpenApiResponse(
                description="Stripe verification unavailable; retry safely."
            ),
        },
    )
    def get(self, request):
        payment = verify_payment(session_payment(request))
        return Response(
            {"detail": "Payment confirmed.", "payment": PaymentSerializer(payment).data}
        )


class CancelView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=["Payments"],
        auth=[],
        summary="Leave checkout without paying",
        description="Public Stripe redirect. Does not change payment status or inventory and does not expire the session. A pending payment can be resumed at session_url until Stripe expires it. Already paid payments remain PAID.",
        parameters=[OpenApiParameter("session_id", str, required=True)],
        responses={
            200: PaymentResultSerializer,
            400: OpenApiResponse(description="Missing session_id."),
            404: OpenApiResponse(description="Unknown session."),
        },
    )
    def get(self, request):
        payment = session_payment(request)
        return Response(
            {
                "detail": (
                    "You can continue payment using session_url."
                    if payment.status == Payment.Status.PENDING
                    else (
                        "Payment already confirmed."
                        if payment.status == Payment.Status.PAID
                        else "Checkout expired. The owner or staff can renew payment."
                    )
                ),
                "payment": PaymentSerializer(payment).data,
            }
        )


class RenewView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PaymentSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Payment.objects.none()
        queryset = Payment.objects.all()
        if not self.request.user.is_staff:
            queryset = queryset.filter(borrowing__user=self.request.user)
        return queryset

    @extend_schema(
        tags=["Payments"],
        summary="Renew an expired Checkout session",
        request=None,
        description="JWT required. Owner or staff only. EXPIRED payments only; PAID and ordinary PENDING payments return 409. Use a new UUID Idempotency-Key for each renewal, reusing it for retries. The server reuses the stored total/currency, reserves a generation atomically, then calls Stripe outside the transaction. Does not create a borrowing or change inventory. Old session callbacks return 404. Unknown outcomes older than 23h require manual reconciliation.",
        parameters=[
            OpenApiParameter(
                "Idempotency-Key", str, OpenApiParameter.HEADER, required=True
            )
        ],
        responses={
            200: PaymentSerializer,
            400: OpenApiResponse(description="Missing/invalid UUID header."),
            401: OpenApiResponse(description="JWT required."),
            404: OpenApiResponse(
                description="Payment not found or belongs to another user."
            ),
            409: OpenApiResponse(
                description="Not expired, key conflict or unsafe retry window."
            ),
            503: OpenApiResponse(
                description="Stripe unavailable; retry the same renewal key."
            ),
        },
    )
    def post(self, request, *args, **kwargs):
        payment = self.get_object()
        try:
            key = uuid.UUID(request.headers.get("Idempotency-Key", ""))
        except ValueError, TypeError, AttributeError:
            raise serializers.ValidationError(
                {"Idempotency-Key": "A UUID header is required."}
            )
        return Response(self.get_serializer(renew_checkout(payment, key)).data)
