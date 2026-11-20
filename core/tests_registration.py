"""
Run with:  python manage.py test core.tests_registration
Storage, OCR and the face engine are replaced by the stand-ins in core/testing.py.
"""
import hashlib
import io
import json
import re
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from . import registration, registration_fields as rf
from .idscan import faces, mapping, storage
from .models import (
    IDDocument, RegistrationDraft, User, VerificationKind as Kind, VerificationOutcome as Outcome, VerificationRecord,
)
from .testing import ANA, BEN, FAKE_ENGINES, FakeOCR, fake_storage, lines, picture

PASSWORD = "Strong@123"
CARD = lines(
    "BATANGAS STATE UNIVERSITY",
    "DELA CRUZ, JUAN SANTOS JR.",
    "SR-CODE: 21-12345",
    "BS Information Technology",
    "Date of Birth: 05/17/2004",
    # Printed on some cards, but never autofilled:
    "Email: juan@g.batstate-u.edu.ph",
    "Mobile: 0917 123 4567",
)
PLAIN_TEMPLATE = {"plain": {"name_format": "auto", "student_id_pattern": r"\b\d{2}-\d{5}\b", "labels": {"name": ("name",)}}}


def post(client, url, data=None):
    return client.post(url, data=json.dumps(data or {}), content_type="application/json")


@override_settings(**FAKE_ENGINES, ID_BUCKET="test-bucket")
class RegistrationCase(TestCase):
    def setUp(self):
        cache.clear()
        FakeOCR.reset()
        FakeOCR.lines = CARD
        context = fake_storage()
        self.files = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def upload(self, data=None, side="front", content_type="image/png", client=None):
        """Ask for an upload, then put the file where the browser would have sent it."""
        data = picture() if data is None else data
        res = post(client or self.client, "/api/registration/upload-id/",
                   {"side": side, "content_type": content_type, "size": len(data)})
        self.assertEqual(res.status_code, 200, res.content)
        self.files[res.json()["upload"]["fields"]["key"]] = data
        return res

    def extract(self, client=None):
        return post(client or self.client, "/api/registration/extract-id/")

    def read_id(self, **kwargs):
        self.upload(**kwargs)
        res = self.extract(kwargs.get("client"))
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def form(self, **overrides):
        data = {
            "first_name": "Juan", "middle_name": "Santos", "last_name": "Dela Cruz", "suffix": "Jr",
            "student_id_number": "21-12345", "program": "BS Information Technology", "year_level": "3",
            "email": "juan@g.batstate-u.edu.ph", "mobile": "0917 123 4567", "user_type": "student",
            "password": PASSWORD, "confirm_password": PASSWORD, "consent": True,
        }
        data.update(overrides)
        return data

    def register(self, client=None, **overrides):
        return post(client or self.client, "/api/auth/register/", self.form(**overrides))


class AutofillTests(RegistrationCase):
    def test_reads_only_id_derived_fields(self):
        data = self.read_id()
        self.assertEqual({name: item["value"] for name, item in data["fields"].items()}, {
            "last_name": "Dela Cruz", "first_name": "Juan", "middle_name": "Santos", "suffix": "Jr",
            "student_id_number": "21-12345", "program": "BS Information Technology", "date_of_birth": "2004-05-17",
        })
        self.assertTrue(set(data["fields"]) <= rf.ID_DERIVED)
        self.assertFalse(set(data["fields"]) & rf.MANUAL_ONLY)
        self.assertTrue(all(0 < item["confidence"] <= 1 for item in data["fields"].values()))
        self.assertEqual(data["name"], {"format": "comma", "low_confidence": False})
        self.assertEqual(data["face"], {"found": True})

    def test_manual_fields_are_blocked_even_if_the_mapper_returns_them(self):
        leaked = mapping.Extraction(fields={
            "first_name": {"value": "Juan", "confidence": 0.9},
            "email": {"value": "juan@g.batstate-u.edu.ph", "confidence": 0.9},
            "mobile": {"value": "+639171234567", "confidence": 0.9},
            "user_type": {"value": "student", "confidence": 0.9},
            "password": {"value": "Secret@123", "confidence": 0.9},
            "consent": {"value": "true", "confidence": 0.9},
        })
        with patch.object(mapping, "map_fields", return_value=leaked):
            data = self.read_id()
        self.assertEqual(list(data["fields"]), ["first_name"])

    def test_missing_middle_name_is_left_out_not_sent_empty(self):
        FakeOCR.lines = lines("REYES, ANA", "SR-CODE: 21-00001")
        self.upload()
        res = self.extract()
        self.assertEqual(set(res.json()["fields"]), {"last_name", "first_name", "student_id_number"})
        self.assertNotIn("middle_name", res.content.decode())
        for item in res.json()["fields"].values():
            self.assertNotIn(item["value"].lower(), ("", "null", "n/a"))

    def test_partial_ocr_leaves_blanks_and_registration_still_works(self):
        FakeOCR.lines = lines("REYES, ANA")
        data = self.read_id()
        self.assertEqual(set(data["fields"]), {"last_name", "first_name"})
        self.assertIn(registration.SOME_TEXT, data["warnings"])
        res = self.register(first_name="Ana", middle_name="", last_name="Reyes", suffix="")
        self.assertEqual(res.status_code, 201, res.content)

    def test_failed_ocr_gives_an_empty_form_and_registration_still_works(self):
        FakeOCR.fail = True
        data = self.read_id()
        self.assertEqual((data["fields"], data["warnings"]), ({}, [registration.NO_TEXT]))
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(IDDocument.objects.get().raw_name, "")

    @override_settings(ID_TEMPLATE="plain", ID_TEMPLATES=PLAIN_TEMPLATE)
    def test_name_without_a_comma_is_a_marked_guess(self):
        FakeOCR.lines = lines("JUAN DELA CRUZ JR")
        data = self.read_id()
        self.assertEqual({name: item["value"] for name, item in data["fields"].items()},
                         {"first_name": "Juan", "last_name": "Dela Cruz", "suffix": "Jr"})
        self.assertEqual(data["name"], {"format": "no_comma", "low_confidence": True})
        self.assertIn(registration.CHECK_NAME, data["warnings"])
        self.assertLess(data["fields"]["last_name"]["confidence"], 0.6)

    def test_back_of_the_card_fills_gaps_but_the_front_wins(self):
        FakeOCR.pages = {  # by picture width
            200: lines("REYES, ANA", "SR-CODE: 21-00001"),
            240: lines("Address: 123 Rizal St, Batangas City", "SR-CODE: 99-99999"),
        }
        self.upload(picture(size=(200, 160)))
        self.upload(picture(faces=0, size=(240, 160)), side="back")
        fields = self.extract().json()["fields"]
        self.assertEqual(fields["address"]["value"], "123 Rizal St, Batangas City")
        self.assertEqual(fields["student_id_number"]["value"], "21-00001")

    def test_schema_endpoint_serves_the_classification(self):
        data = self.client.get("/api/registration/schema/").json()
        sources = {field["name"]: field["source"] for field in data["fields"]}
        self.assertEqual({name for name, source in sources.items() if source == "id"}, set(rf.ID_DERIVED))
        self.assertEqual(sources["email"], "manual")
        self.assertEqual(data["upload"]["types"], ["image/jpeg", "image/png", "application/pdf"])


class UploadTests(RegistrationCase):
    def test_no_face_on_the_id_fails_the_step(self):
        self.upload(picture(faces=0))
        res = self.extract()
        self.assertEqual((res.status_code, res.json()["code"]), (422, "no_face_on_id"))
        self.assertEqual(res.json()["message"], registration.NO_FACE)
        self.assertEqual(VerificationRecord.objects.get().outcome, Outcome.NO_FACE)
        res = self.register()
        self.assertEqual((res.status_code, res.json()["code"]), (409, "no_reference_face"))
        self.assertEqual(User.objects.count(), 0)

    def test_portrait_too_small_to_use(self):
        self.upload(picture(size=(100, 80)))  # the face would be 50 pixels wide
        self.assertEqual(self.extract().json()["code"], "no_face_on_id")

    def test_file_is_judged_by_its_signature_not_its_name(self):
        for data in (b"<html>not an image</html>", b"\x89PNG\r\n\x1a\n" + b"damaged" * 20, b"%PDF-1.7 damaged", b"MZ\x90\x00"):
            self.upload(data)
            res = self.extract()
            self.assertEqual((res.status_code, res.json()["code"]), (400, "invalid_file"), data[:12])
        self.assertEqual(set(VerificationRecord.objects.values_list("outcome", flat=True)), {Outcome.BAD_FILE})

    def test_real_type_wins_over_the_declared_one(self):
        self.read_id(data=picture(fmt="JPEG"), content_type="image/png")
        self.assertEqual(IDDocument.objects.get().content_type, "image/jpeg")

    def test_pdf_is_accepted(self):
        pdf = io.BytesIO()
        Image.open(io.BytesIO(picture(size=(400, 320)))).save(pdf, "PDF")
        data = self.read_id(data=pdf.getvalue(), content_type="application/pdf")
        self.assertTrue(data["face"]["found"])

    def test_declared_type_size_and_side_are_checked(self):
        cases = (
            ({"side": "front", "content_type": "image/gif", "size": 10}, "file"),
            ({"side": "front", "content_type": "image/png", "size": 0}, "file"),
            ({"side": "front", "content_type": "image/png", "size": 9 * 1024 * 1024}, "file"),
            ({"side": "front", "content_type": "image/png", "size": "big"}, "file"),
            ({"side": "left", "content_type": "image/png", "size": 10}, "side"),
        )
        for body, field in cases:
            res = post(self.client, "/api/registration/upload-id/", body)
            self.assertEqual(res.status_code, 400, body)
            self.assertIn(field, res.json()["errors"], body)
        self.assertEqual(IDDocument.objects.count(), 0)

    def test_file_that_never_arrived(self):
        self.assertEqual(self.extract().json()["code"], "upload_missing")
        post(self.client, "/api/registration/upload-id/", {"side": "front", "content_type": "image/png", "size": 10})
        res = self.extract()
        self.assertEqual((res.status_code, res.json()["code"]), (404, "upload_missing"))

    def test_file_larger_than_it_claimed_is_refused(self):
        key = self.upload().json()["upload"]["fields"]["key"]
        self.files[key] = b"\xff\xd8\xff" + b"0" * (8 * 1024 * 1024)  # more than was declared and allowed
        self.assertEqual(self.extract().json()["code"], "invalid_file")

    def test_uploading_again_replaces_the_earlier_file(self):
        first = self.upload().json()["upload"]["fields"]["key"]
        self.upload(picture(faces=0, person=BEN), side="back")
        second = self.upload(picture(person=BEN)).json()["upload"]["fields"]["key"]
        self.assertEqual(list(self.files), [second])  # a new front is a new card: its old back went too
        self.assertNotEqual(first, second)
        self.assertEqual(IDDocument.objects.count(), 1)
        self.assertTrue(second.startswith(f"pending/{RegistrationDraft.objects.get().pk}/id/front-"))

    def test_a_new_front_voids_the_earlier_portrait(self):
        self.read_id()
        self.assertIsNotNone(RegistrationDraft.objects.get().face_template)
        self.upload(picture(faces=0))
        self.assertIsNone(RegistrationDraft.objects.get().face_template)
        self.assertEqual(self.register().json()["code"], "no_reference_face")

    @override_settings(ID_EXTRACTS_PER_DRAFT=2)
    def test_tries_per_sign_up_are_limited(self):
        self.upload()
        self.assertEqual([self.extract().status_code for _ in range(3)], [200, 200, 429])

    @override_settings(ID_EXTRACTS_PER_IP_PER_HOUR=1)
    def test_tries_per_network_are_limited(self):
        self.read_id()
        other = Client()
        self.upload(picture(person=BEN), client=other)
        self.assertEqual(self.extract(other).status_code, 429)

    def test_storage_not_set_up_or_down(self):
        with patch.object(storage, "is_configured", return_value=False):
            res = post(self.client, "/api/registration/upload-id/", {"side": "front", "content_type": "image/png", "size": 10})
            self.assertEqual(res.status_code, 503)
        self.upload()
        with patch.object(storage, "fetch", side_effect=storage.StorageUnavailable("down")):
            self.assertEqual(self.extract().status_code, 503)


class RegisterTests(RegistrationCase):
    def test_creates_the_account_from_the_confirmed_form(self):
        self.read_id()
        res = self.register()
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["message"], "A verification link has been sent to your school email.")
        user = User.objects.get()
        self.assertEqual((user.first_name, user.middle_name, user.last_name, user.suffix), ("Juan", "Santos", "Dela Cruz", "Jr"))
        self.assertEqual((user.full_name, user.short_name), ("Juan Santos Dela Cruz Jr", "Juan Dela Cruz"))
        self.assertEqual((user.student_id_number, user.program, user.year_level), ("21-12345", "BS Information Technology", "3"))
        self.assertEqual((user.email, user.mobile, user.user_type), ("juan@g.batstate-u.edu.ph", "+639171234567", "student"))
        self.assertNotIn(PASSWORD, user.password)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertFalse(user.is_email_verified)
        self.assertIsNotNone(user.consent_at)
        self.assertIn("/verify-email/", mail.outbox[0].body)

        # The reference face: an encrypted template, not readable without the key, and no image anywhere.
        self.assertEqual(user.face_model, "fake-faces")
        self.assertEqual(faces.unseal(user.face_template).tolist(), [0, 1, 0, 0, 0, 0, 0, 0])
        self.assertNotIn(bytes(faces.unseal(user.face_template).tobytes()), bytes(user.face_template))
        self.assertEqual(self.files, {})

        document = IDDocument.objects.get()
        self.assertEqual((document.user, document.draft, document.side), (user, None, "front"))
        self.assertEqual(document.sha256, hashlib.sha256(picture()).hexdigest())
        self.assertEqual((document.raw_name, document.name_format), ("DELA CRUZ, JUAN SANTOS JR.", "comma"))
        self.assertIsNotNone(document.image_deleted_at)
        self.assertNotIn("Juan", json.dumps(document.field_confidence))  # confidences only, no values
        self.assertEqual(RegistrationDraft.objects.count(), 0)
        self.assertEqual(VerificationRecord.objects.get().outcome, Outcome.SUCCESS)

    def test_the_persons_edits_win(self):
        self.read_id()  # the card says JUAN SANTOS DELA CRUZ JR.
        self.register(first_name="Juanito", middle_name="", last_name="Dela Cruz-Reyes")
        user = User.objects.get()
        self.assertEqual((user.first_name, user.middle_name, user.last_name, user.suffix),
                         ("Juanito", "", "Dela Cruz-Reyes", "Jr"))

    def test_every_field_is_checked_whether_typed_or_autofilled(self):
        self.read_id()
        res = self.register(
            first_name="Juan2", middle_name="-", last_name="N/A", suffix="ElevenChars", student_id_number="",
            program="", year_level="9", date_of_birth="2999-01-01", sex="x", email="juan@gmail.com", mobile="12345",
            password="weak", confirm_password="other", consent=False,
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(set(res.json()["errors"]), {
            "first_name", "middle_name", "last_name", "suffix", "student_id_number", "program", "year_level",
            "date_of_birth", "sex", "email", "mobile", "password", "confirm_password", "consent",
        })
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(self.register(consent="yes").json()["errors"], {"consent": "Please agree before continuing."})
        self.assertEqual(self.register(user_type="").json()["errors"], {"user_type": "Please fill out this field."})

    def test_program_and_year_are_only_required_of_students(self):
        self.read_id()
        self.assertEqual(self.register(user_type="faculty", program="", year_level="").status_code, 201)

    def test_names_single_initial_accents_and_null_text(self):
        self.read_id()
        self.assertEqual(self.register(first_name="Niño", middle_name="D", last_name="Peña-O'Brien", suffix="jr.").status_code, 201)
        user = User.objects.get()
        self.assertEqual((user.first_name, user.middle_name, user.last_name, user.suffix), ("Niño", "D", "Peña-O'Brien", "Jr"))

        other = Client()
        self.read_id(data=picture(person=BEN), client=other)
        res = self.register(other, middle_name="null", suffix="", student_id_number="21-00002",
                            email="ben@g.batstate-u.edu.ph", mobile="0917 222 2222")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(User.objects.get(email="ben@g.batstate-u.edu.ph").middle_name, "")

    def test_needs_an_id_that_was_read(self):
        res = self.register()
        self.assertEqual((res.status_code, res.json()["code"]), (409, "no_reference_face"))
        self.upload()  # uploaded, never read
        self.assertEqual(self.register().json()["code"], "no_reference_face")

    def test_unfinished_sign_ups_expire_and_are_purged(self):
        self.read_id()
        RegistrationDraft.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.register().json()["code"], "no_reference_face")
        self.assertEqual(len(self.files), 1)
        self.assertEqual(registration.purge_expired_drafts(), 1)
        self.assertEqual((self.files, IDDocument.objects.count(), RegistrationDraft.objects.count()), ({}, 0, 0))

    def test_one_account_per_id_number(self):
        self.read_id()
        self.assertEqual(self.register().status_code, 201)
        other = Client()
        self.read_id(data=picture(person=BEN), client=other)
        res = self.register(other, student_id_number=" 21-12345 ", email="ben@g.batstate-u.edu.ph", mobile="0917 222 2222")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(list(res.json()["errors"]), ["student_id_number"])
        self.assertEqual(User.objects.count(), 1)

    def test_one_account_per_id_image(self):
        self.read_id()
        self.assertEqual(self.register().status_code, 201)
        other = Client()
        self.upload(client=other)  # byte-for-byte the same file
        res = self.extract(other)
        self.assertEqual((res.status_code, res.json()["code"]), (409, "duplicate_id"))
        self.assertTrue(VerificationRecord.objects.filter(outcome=Outcome.DUPLICATE).exists())

    def test_duplicate_email_and_mobile(self):
        self.read_id()
        self.register()
        other = Client()
        self.read_id(data=picture(person=BEN), client=other)
        res = self.register(other, student_id_number="21-00002")
        self.assertEqual(set(res.json()["errors"]), {"email", "mobile"})

    @override_settings(ID_IMAGE_RETENTION_DAYS=7)
    def test_retention_keeps_the_image_under_the_users_own_path(self):
        self.read_id()
        self.register()
        user, document = User.objects.get(), IDDocument.objects.get()
        self.assertEqual(list(self.files), [document.storage_key])
        self.assertTrue(document.storage_key.startswith(f"users/{user.pk}/id/front-"))
        self.assertIsNone(document.image_deleted_at)

    def test_cleanup_failure_does_not_undo_the_registration(self):
        self.read_id()
        with patch.object(storage, "delete", side_effect=storage.StorageUnavailable("down")):
            self.assertEqual(self.register().status_code, 201)
        self.assertIsNone(IDDocument.objects.get().image_deleted_at)

    def test_after_registering_login_still_needs_the_face(self):
        self.read_id()
        self.register()
        link = re.search(r"/verify-email/([^/\s]+)/", mail.outbox[0].body).group(1)
        self.client.get(f"/verify-email/{link}/")
        res = post(self.client, "/api/auth/login/", {"email": "juan@g.batstate-u.edu.ph", "password": PASSWORD})
        self.assertEqual(res.json()["next"], "face_scan")
        self.assertEqual(self.client.get("/api/me/").status_code, 401)

    def test_pages_render_from_the_field_list(self):
        page = self.client.get("/create-account/").content.decode()
        for field in rf.FIELDS:
            self.assertIn(f'name="{field.name}"', page, field.name)
        self.assertEqual(page.count('autocomplete="new-password"'), 2)
        self.assertIn('id="registration-schema"', page)
        self.assertIn('id="face-step"', self.client.get("/login/").content.decode())
