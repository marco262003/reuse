"""Create notifications and deliver them through web, email and SMS."""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Delivery, DeliveryChannel, DeliveryStatus, Notification
from .sms import SMSNotConfigured, send_sms

log = logging.getLogger(__name__)

ALL_CHANNELS = ("web", "email", "sms")


def create_notification(user, ntype, message, *, request=None, borrowing=None, item=None,
                        dedupe_key=None, channels=ALL_CHANNELS):
    """
    Save the web notification first (it is never lost), then queue email/SMS
    for after the database transaction commits.
    Returns None when dedupe_key shows this notification already exists.
    """
    if dedupe_key and Notification.objects.filter(user=user, dedupe_key=dedupe_key).exists():
        return None
    try:
        with transaction.atomic():
            note = Notification.objects.create(
                user=user, type=ntype, message=message, request=request,
                borrowing=borrowing, item=item, dedupe_key=dedupe_key,
            )
            Delivery.objects.create(
                notification=note, channel=DeliveryChannel.WEB,
                status=DeliveryStatus.SENT, attempts=1, attempted_at=timezone.now(),
            )
    except IntegrityError:  # another worker created the same reminder first
        return None

    external = tuple(c for c in channels if c != "web")
    if external:
        transaction.on_commit(lambda: deliver_external(note.pk, external))
    return note


def deliver_external(notification_id, channels):
    try:
        note = Notification.objects.select_related("user").get(pk=notification_id)
    except Notification.DoesNotExist:
        return
    for channel in channels:
        deliver_one(note, channel)


def deliver_one(note, channel):
    """Send one notification on one channel. A failure never touches the saved web notification."""
    delivery, _ = Delivery.objects.get_or_create(notification=note, channel=channel)
    if delivery.status == DeliveryStatus.SENT:  # already delivered: never send twice
        return delivery

    user = note.user
    delivery.attempts += 1
    delivery.attempted_at = timezone.now()

    if channel == DeliveryChannel.EMAIL and not (user.notify_email and user.email):
        return _finish(delivery, DeliveryStatus.SKIPPED, "Email notifications are off for this user.")
    if channel == DeliveryChannel.SMS and not (user.notify_sms and user.mobile):
        return _finish(delivery, DeliveryStatus.SKIPPED, "SMS notifications are off or no mobile number.")

    try:
        if channel == DeliveryChannel.EMAIL:
            send_mail("REUSE notification", note.message, settings.DEFAULT_FROM_EMAIL, [user.email])
        else:
            send_sms(user.mobile, f"REUSE: {note.message}")
        return _finish(delivery, DeliveryStatus.SENT)
    except SMSNotConfigured:
        return _finish(delivery, DeliveryStatus.SKIPPED, "SMS provider is not configured.")
    except Exception as exc:  # noqa: BLE001 - recorded, can be retried by the scheduled command
        log.exception("Could not deliver notification %s via %s", note.pk, channel)
        return _finish(delivery, DeliveryStatus.FAILED, str(exc)[:300])


def _finish(delivery, status, error=""):
    delivery.status = status
    delivery.error = error
    delivery.save(update_fields=["status", "error", "attempts", "attempted_at"])
    return delivery
