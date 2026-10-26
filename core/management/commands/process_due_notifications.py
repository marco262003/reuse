"""
Run every 10-15 minutes (cron / Task Scheduler):

    python manage.py process_due_notifications

Creates "due soon" and "overdue" notifications (once each) and retries
email/SMS deliveries that failed.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Borrowing, BorrowingStatus, Delivery, DeliveryStatus, NotificationType
from core.notifications import create_notification, deliver_one


class Command(BaseCommand):
    help = "Create due-soon / overdue notifications and retry failed deliveries."

    def handle(self, *args, **options):
        now = timezone.now()
        soon = now + timedelta(hours=settings.DUE_SOON_HOURS)
        created = 0

        active = Borrowing.objects.filter(status=BorrowingStatus.ACTIVE).select_related("user", "item")
        for b in active:
            if b.due_at <= now:
                note = create_notification(
                    b.user, NotificationType.OVERDUE,
                    f"{b.item.name} is overdue. Please return it as soon as possible.",
                    borrowing=b, dedupe_key=f"overdue:{b.pk}",
                )
            elif b.due_at <= soon:
                due_text = timezone.localtime(b.due_at).strftime("%B %d, %Y at %I:%M %p")
                note = create_notification(
                    b.user, NotificationType.DUE_SOON,
                    f"Reminder: {b.item.name} is due for return on {due_text}.",
                    borrowing=b, dedupe_key=f"due_soon:{b.pk}",
                )
            else:
                continue
            created += 1 if note else 0

        retried = 0
        failed = Delivery.objects.filter(status=DeliveryStatus.FAILED, attempts__lt=3).select_related("notification__user")
        for delivery in failed:
            deliver_one(delivery.notification, delivery.channel)
            retried += 1

        self.stdout.write(self.style.SUCCESS(f"{created} reminder(s) created, {retried} delivery retry(ies)."))
