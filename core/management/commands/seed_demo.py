"""Create demo data for local testing:  python manage.py seed_demo"""
from django.core.management.base import BaseCommand

from core.models import Category, Item, User


ITEMS = [
    ("Monobloc Chair", Category.TOOLS, 90, "Stackable plastic chair for events.", "good"),
    ("Projector", Category.AV, 4, "HD projector with HDMI and VGA input.", "good"),
    ("HDMI Cable (3 m)", Category.AV, 10, "3-meter HDMI cable.", "fair"),
    ("Extension Cord", Category.TOOLS, 8, "5-outlet extension cord.", "good"),
    ("Basketball", Category.SPORTS, 6, "Size 7 indoor/outdoor basketball.", "fair"),
    ("Volleyball Net", Category.SPORTS, 2, "Standard volleyball net with posts.", "good"),
    ("Whiteboard Markers (box)", Category.SUPPLIES, 12, "Box of 12 assorted markers.", "new"),
    ("Reference: Database Systems", Category.BOOKS, 3, "Textbook for IT classes.", "good"),
]


class Command(BaseCommand):
    help = "Creates sample items and a verified demo account."

    def handle(self, *args, **options):
        for name, category, qty, description, condition in ITEMS:
            Item.objects.get_or_create(
                name=name,
                defaults=dict(category=category, total_qty=qty, description=description, condition=condition),
            )
        # One item that is "under repair" so you can see the unavailable state + Notify When Available.
        Item.objects.get_or_create(
            name="Wireless Presenter", defaults=dict(
                category=Category.AV, total_qty=2, repair_qty=2, condition="fair",
                description="Presentation clicker with laser pointer.",
            ),
        )
        if not User.objects.filter(email="demo@g.batstate-u.edu.ph").exists():
            User.objects.create_user(
                email="demo@g.batstate-u.edu.ph", password="Demo@1234", full_name="Juan Dela Cruz",
                mobile="+639171234567", user_type="student", is_email_verified=True,
            )
        self.stdout.write(self.style.SUCCESS(
            "Seeded items. Demo login: demo@g.batstate-u.edu.ph / Demo@1234"
        ))
