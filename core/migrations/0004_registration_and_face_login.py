import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

SUFFIXES = {"JR", "SR", "II", "III", "IV"}
PARTICLES = {"DE", "DEL", "DELA", "DELOS", "DELAS", "LA", "LOS", "LAS", "VAN", "VON", "DER", "DEN", "SAN", "SANTA", "STA", "STO"}


def split_full_name(full_name):
    """First word, last word (with its particles) and whatever is between. Spelling and case are kept."""
    words = full_name.split()
    suffix = words.pop().strip(".,") if len(words) > 2 and words[-1].strip(".,").upper() in SUFFIXES else ""
    start = len(words) - 1
    while start > 1 and words[start - 1].upper() in PARTICLES:
        start -= 1
    if len(words) < 2:
        start = len(words)
    return " ".join(words[:1]), " ".join(words[1:start]), " ".join(words[start:]), suffix


def split_names(apps, schema_editor):
    User = apps.get_model("core", "User")
    for user in User.objects.all():
        first, middle, last, suffix = split_full_name(user.full_name)
        user.first_name, user.middle_name, user.last_name, user.suffix = first[:60], middle[:60], last[:60], suffix[:10]
        user.save(update_fields=["first_name", "middle_name", "last_name", "suffix"])


def join_names(apps, schema_editor):
    User = apps.get_model("core", "User")
    for user in User.objects.all():
        parts = (user.first_name, user.middle_name, user.last_name, user.suffix)
        user.full_name = " ".join(part for part in parts if part)
        user.save(update_fields=["full_name"])


def text(max_length, **kwargs):
    return models.CharField(blank=True, default="", max_length=max_length, **kwargs)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_face_verification"),
    ]

    operations = [
        # --- Name parts replace full_name ---------------------------------------------
        migrations.AddField("user", "first_name", models.CharField(default="", max_length=60), preserve_default=False),
        migrations.AddField("user", "middle_name", text(60), preserve_default=False),
        migrations.AddField("user", "last_name", models.CharField(default="", max_length=60), preserve_default=False),
        migrations.AddField("user", "suffix", text(10), preserve_default=False),
        migrations.RunPython(split_names, join_names),
        migrations.RemoveField("user", "full_name"),

        # --- Fields read from the school ID, and the face template -------------------------
        migrations.AddField("user", "student_id_number", models.CharField(blank=True, max_length=20, null=True, unique=True)),
        migrations.AddField("user", "date_of_birth", models.DateField(blank=True, null=True)),
        migrations.AddField("user", "program", text(120), preserve_default=False),
        migrations.AddField("user", "year_level", text(20), preserve_default=False),
        migrations.AddField("user", "sex", text(10), preserve_default=False),
        migrations.AddField("user", "address", text(255), preserve_default=False),
        migrations.AddField("user", "college", text(120), preserve_default=False),
        migrations.AddField("user", "id_valid_until", text(30), preserve_default=False),
        migrations.AddField("user", "face_template", models.BinaryField(blank=True, editable=False, null=True)),
        migrations.AddField("user", "face_model", text(40), preserve_default=False),
        migrations.AddField("user", "face_enrolled_at", models.DateTimeField(blank=True, null=True)),
        migrations.RenameField("user", "biometric_consent_at", "consent_at"),

        # --- The Amazon-based verification from 0002 and 0003 is gone ----------------------
        migrations.RemoveField("user", "identity_verification_status"),
        migrations.RemoveField("user", "identity_verified"),
        migrations.RemoveField("user", "identity_verified_at"),
        migrations.RemoveField("user", "identity_attempts_reset_at"),
        migrations.RemoveField("user", "reference_photo_key"),
        migrations.RemoveField("user", "reference_face_id"),
        migrations.RemoveField("user", "school_id_hash"),
        migrations.RemoveField("user", "school_id_last4"),
        migrations.DeleteModel("FaceVerificationAttempt"),
        migrations.AlterField(
            "notification", "type",
            models.CharField(max_length=24, choices=[
                ("account_verified", "Account verified"), ("request_approved", "Request approved"),
                ("request_rejected", "Request rejected"), ("reservation_canceled", "Reservation canceled"),
                ("item_available", "Item available"), ("due_soon", "Due soon"), ("overdue", "Overdue"),
                ("return_confirmed", "Return confirmed"), ("sign_in_alert", "Sign-in alert"),
            ]),
        ),
        migrations.AddField(
            "passwordresetcode", "purpose",
            models.CharField(choices=[("reset", "Password reset"), ("login", "Sign-in")], default="reset", max_length=5),
        ),

        # --- Registration drafts, ID document metadata, audit log ---------------------------
        migrations.CreateModel(
            name="RegistrationDraft",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField()),
                ("ip", models.GenericIPAddressField(blank=True, null=True)),
                ("uploads", models.PositiveSmallIntegerField(default=0)),
                ("extractions", models.PositiveSmallIntegerField(default=0)),
                ("face_template", models.BinaryField(blank=True, editable=False, null=True)),
                ("face_model", models.CharField(blank=True, max_length=40)),
            ],
        ),
        migrations.CreateModel(
            name="IDDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("side", models.CharField(choices=[("front", "Front"), ("back", "Back")], max_length=5)),
                ("storage_key", models.CharField(max_length=200)),
                ("content_type", models.CharField(max_length=30)),
                ("size_bytes", models.PositiveIntegerField(default=0)),
                ("sha256", models.CharField(blank=True, db_index=True, max_length=64)),
                ("raw_name", models.TextField(blank=True)),
                ("name_format", models.CharField(blank=True, max_length=10)),
                ("field_confidence", models.JSONField(blank=True, default=dict)),
                ("face_found", models.BooleanField(default=False)),
                ("ocr_engine", models.CharField(blank=True, max_length=40)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("image_deleted_at", models.DateTimeField(blank=True, null=True)),
                ("draft", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="documents", to="core.registrationdraft")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="id_documents", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "constraints": [models.UniqueConstraint(condition=models.Q(("side", "front"), ("user__isnull", False)), fields=("sha256",), name="one_account_per_id_image")],
            },
        ),
        migrations.CreateModel(
            name="VerificationRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("draft_id", models.UUIDField(blank=True, null=True)),
                ("kind", models.CharField(choices=[("id_extract", "ID read"), ("face_login", "Face scan"), ("email_code", "Email code")], max_length=10)),
                ("outcome", models.CharField(choices=[("success", "Success"), ("no_match", "Face did not match"), ("liveness_failed", "Liveness check failed"), ("no_face", "No face found"), ("multiple_faces", "More than one face"), ("bad_file", "Unusable file"), ("duplicate", "ID already registered"), ("wrong_code", "Wrong code"), ("error", "Error")], max_length=15)),
                ("similarity", models.FloatField(blank=True, null=True)),
                ("ip", models.GenericIPAddressField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="verification_records", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [models.Index(fields=["user", "kind", "created_at"], name="core_verifi_user_id_e62766_idx")],
            },
        ),
    ]
