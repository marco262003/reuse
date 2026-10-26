import os

from django.core.management.base import BaseCommand, CommandError

from core.models import User


class Command(BaseCommand):
    help = "Create the initial REUSE staff administrator if it does not already exist."

    def handle(self, *args, **options):
        email = os.environ.get("REUSE_ADMIN_EMAIL", "admin@reuse.com").strip().lower()
        password = os.environ.get("REUSE_ADMIN_PASSWORD", "admin123")
        if User.objects.filter(email__iexact=email).exists():
            self.stdout.write(self.style.WARNING(
                f"Admin account {email} already exists. Its password was NOT changed."
            ))
            return
        if not password:
            raise CommandError("Set REUSE_ADMIN_PASSWORD before creating the administrator.")
        admin = User.objects.create_superuser(
            email=email, password=password, full_name="REUSE Administrator", user_type="staff",
        )
        self.stdout.write(self.style.SUCCESS(f"Created staff administrator: {admin.email}"))
        if password == "admin123":
            self.stdout.write(self.style.WARNING(
                "This is the development default password. Change it before using real accounts."
            ))
