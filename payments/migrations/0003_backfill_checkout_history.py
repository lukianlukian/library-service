from django.db import migrations


def backfill_sessions(apps, schema_editor):
    Payment = apps.get_model("payments", "Payment")
    CheckoutSession = apps.get_model("payments", "CheckoutSession")
    alias = schema_editor.connection.alias
    for payment in (
        Payment.objects.using(alias).exclude(session_id__isnull=True).iterator()
    ):
        CheckoutSession.objects.using(alias).get_or_create(
            session_id=payment.session_id,
            defaults={
                "payment_id": payment.pk,
                "request_key": payment.request_key,
                "session_url": payment.session_url,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0002_payment_renewal_key_payment_renewal_started_at_and_more")
    ]
    operations = [migrations.RunPython(backfill_sessions, migrations.RunPython.noop)]
