from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from payments.models import Payment
from payments.serializers import PaymentResultSerializer, PaymentSerializer
from payments.services import verify_payment


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
                    else "Payment already confirmed."
                ),
                "payment": PaymentSerializer(payment).data,
            }
        )
