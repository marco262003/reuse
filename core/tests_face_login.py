"""
Run with:  python manage.py test core.tests_face_login
The face engine is replaced by the stand-in in core/testing.py, which "sees" what a test
picture's colour says: how many faces, how far the head is turned, and whose face it is.
"""
import json
import re
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from . import face_login
from .api_auth import FACE_MESSAGES
from .models import (
    Notification, NotificationType, PasswordResetCode, User, VerificationKind as Kind,
    VerificationOutcome as Outcome, VerificationRecord,
)
from .testing import ANA, BEN, FAKE_ENGINES, FakeOCR, fake_storage, frame, lines, picture, template_for

PASSWORD = "Strong@123"
LIVE = {"center": 0.0, "left": 0.25, "right": -0.25}  # how a real head moves for each prompt
REAL_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher", "django.contrib.auth.hashers.PBKDF2PasswordHasher"]


def post(client, url, data=None):
    return client.post(url, data=json.dumps(data or {}), content_type="application/json")


def make_user(email="ana@g.batstate-u.edu.ph", mobile="+639171111111", person=ANA, **extra):
    enrolled = {"face_template": template_for(person), "face_model": "fake-faces", "face_enrolled_at": timezone.now()} if person else {}
    return User.objects.create_user(
        email=email, password=PASSWORD, first_name="Ana", last_name="Cruz", mobile=mobile,
        user_type="student", is_email_verified=True, **enrolled, **extra,
    )


@override_settings(**FAKE_ENGINES, ID_BUCKET="test-bucket")
class FaceLoginCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()

    def password_step(self, client=None, user=None, password=PASSWORD):
        return post(client or self.client, "/api/auth/login/", {"email": (user or self.user).email, "password": password})

    def frames_for(self, steps, person=ANA, turns=LIVE, straight_person=None, faces=1):
        """Two frames per step, the way the browser sends them."""
        frames = []
        for i, step in enumerate(steps):
            who = straight_person if straight_person and step == "center" else person
            frames += [{"step": i, "image": frame(person=who, turn=turns[step], faces=faces)}] * 2
        return frames

    def scan(self, client=None, **kwargs):
        client = client or self.client
        challenge = post(client, "/api/auth/face-scan/").json()
        return post(client, "/api/auth/face-verify/", {
            "challenge_id": challenge["challenge_id"], "frames": self.frames_for(challenge["steps"], **kwargs),
        })

    def logged_in(self, client=None):
        return (client or self.client).get("/api/me/").status_code == 200

    def last_record(self):
        return VerificationRecord.objects.filter(user=self.user).first()

    def emailed_code(self):
        return re.search(r"sign-in code is (\d{6})", mail.outbox[-1].body).group(1)


class FaceScanTests(FaceLoginCase):
    def test_matching_live_face_logs_in(self):
        res = self.password_step()
        self.assertEqual(res.json(), {"ok": True, "next": "face_scan", "attempts_left": 3})
        self.assertFalse(self.logged_in())  # a correct password alone is not a login

        res = self.scan()
        self.assertEqual((res.status_code, res.json()), (200, {"ok": True, "redirect": "/dashboard/"}))
        self.assertTrue(self.logged_in())
        self.assertNotIn("pending_login", self.client.session)
        record = self.last_record()
        self.assertEqual((record.kind, record.outcome, record.ip), (Kind.FACE_LOGIN, Outcome.SUCCESS, "127.0.0.1"))
        self.assertAlmostEqual(record.similarity, 1.0)

    def test_someone_elses_face_is_rejected(self):
        self.password_step()
        res = self.scan(person=BEN)
        self.assertEqual(res.status_code, 401)
        self.assertEqual((res.json()["code"], res.json()["attempts_left"]), ("not_confirmed", 2))
        self.assertFalse(self.logged_in())
        self.assertEqual((self.last_record().outcome, self.last_record().similarity), (Outcome.NO_MATCH, 0.0))
        self.assertNotIn("similarity", res.content.decode())  # the score never leaves the server

    def test_static_image_fails_liveness(self):
        self.password_step()
        still = {"center": 0.0, "left": 0.0, "right": 0.0}  # the right face, but it never turns: a photo
        res = self.scan(turns=still)
        self.assertEqual((res.status_code, res.json()["code"]), (401, "not_confirmed"))
        self.assertEqual(self.last_record().outcome, Outcome.LIVENESS_FAILED)
        self.assertFalse(self.logged_in())
        # From outside, a failed liveness check looks exactly like a mismatch.
        self.assertEqual(res.json()["message"], FACE_MESSAGES["not_confirmed"])
        self.assertEqual(self.scan(person=BEN).json()["message"], FACE_MESSAGES["not_confirmed"])

    def test_tilted_flat_photo_and_wrong_direction_fail_liveness(self):
        self.password_step()
        barely = {"center": 0.0, "left": 0.05, "right": -0.05}  # a flat picture tilted in front of the camera
        self.assertEqual(self.scan(turns=barely).json()["code"], "not_confirmed")
        wrong_way = {"center": 0.0, "left": -0.25, "right": 0.25}
        self.assertEqual(self.scan(turns=wrong_way).json()["code"], "not_confirmed")
        self.assertEqual(set(VerificationRecord.objects.values_list("outcome", flat=True)), {Outcome.LIVENESS_FAILED})

    def test_photo_for_the_straight_frames_and_someone_else_turning(self):
        self.password_step()
        res = self.scan(person=BEN, straight_person=ANA)
        self.assertEqual(res.json()["code"], "not_confirmed")
        self.assertEqual(self.last_record().outcome, Outcome.LIVENESS_FAILED)

    def test_no_face_or_several_faces_ask_for_a_retake_without_costing_a_try(self):
        self.password_step()
        res = self.scan(faces=0)
        self.assertEqual((res.status_code, res.json()["code"], res.json()["attempts_left"]), (422, "no_face", 3))
        res = self.scan(faces=2)
        self.assertEqual((res.status_code, res.json()["code"], res.json()["attempts_left"]), (422, "multiple_faces", 3))
        self.assertEqual(self.scan().status_code, 200)

    @override_settings(FACE_UNUSABLE_SCANS_PER_SIGN_IN=1)
    def test_unusable_scans_are_capped(self):
        self.password_step()
        self.assertEqual(self.scan(faces=0).json()["code"], "no_face")
        self.assertEqual(self.scan(faces=0).json()["code"], "not_confirmed")

    def test_challenge_works_once_and_only_for_its_own_id(self):
        self.password_step()
        challenge = post(self.client, "/api/auth/face-scan/").json()
        self.assertEqual(challenge["steps"][0], "center")
        self.assertEqual(len([s for s in challenge["steps"] if s != "center"]), 2)
        frames = self.frames_for(challenge["steps"])
        res = post(self.client, "/api/auth/face-verify/", {"challenge_id": "someone-elses", "frames": frames})
        self.assertEqual((res.status_code, res.json()["code"]), (410, "challenge_expired"))
        res = post(self.client, "/api/auth/face-verify/", {"challenge_id": challenge["challenge_id"], "frames": frames})
        self.assertEqual(res.status_code, 410)  # the wrong guess used it up
        self.assertFalse(self.logged_in())

    def test_challenge_expires(self):
        self.password_step()
        challenge = post(self.client, "/api/auth/face-scan/").json()
        session = self.client.session
        state = session["pending_login"]
        state["challenge"]["exp"] = 0
        session["pending_login"] = state
        session.save()
        res = post(self.client, "/api/auth/face-verify/", {
            "challenge_id": challenge["challenge_id"], "frames": self.frames_for(challenge["steps"]),
        })
        self.assertEqual(res.status_code, 410)

    def test_bad_frames_are_ignored_not_fatal(self):
        self.password_step()
        challenge = post(self.client, "/api/auth/face-scan/").json()
        junk = [None, "x", {"step": 99, "image": frame()}, {"step": 0, "image": "data:image/png;base64,@@@"}, {"step": True}]
        res = post(self.client, "/api/auth/face-verify/", {"challenge_id": challenge["challenge_id"], "frames": junk})
        self.assertEqual((res.status_code, res.json()["code"]), (422, "no_face"))
        self.assertEqual(post(self.client, "/api/auth/face-verify/", {"challenge_id": "x", "frames": []}).status_code, 400)

    def test_face_steps_need_a_correct_password_first(self):
        for url in ("face-scan", "face-verify", "enroll-id", "login-code/send", "login-code/verify"):
            res = post(Client(), f"/api/auth/{url}/", {"code": "123456", "frames": [1], "consent": True})
            self.assertEqual((res.status_code, res.json().get("redirect")), (401, "/login/"), url)
        self.password_step(password="wrong")
        self.assertEqual(post(self.client, "/api/auth/face-scan/").status_code, 401)

    def test_pending_sign_in_expires_and_ends_if_the_account_is_deactivated(self):
        self.password_step()
        session = self.client.session
        session["pending_login"] = {**session["pending_login"], "exp": 0}
        session.save()
        self.assertEqual(post(self.client, "/api/auth/face-scan/").status_code, 401)

        self.password_step()
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(post(self.client, "/api/auth/face-scan/").status_code, 401)

    def test_profile_api_never_exposes_the_template(self):
        self.password_step()
        self.scan()
        me = self.client.get("/api/me/").json()
        self.assertTrue(me["user"]["face_login"])
        self.assertNotIn("template", json.dumps(me))


class FallbackTests(FaceLoginCase):
    def test_three_failed_scans_switch_to_an_email_code(self):
        self.password_step()
        answers = [self.scan(person=BEN) for _ in range(3)]
        self.assertEqual([res.status_code for res in answers], [401, 401, 423])
        self.assertEqual((answers[2].json()["code"], answers[2].json()["next"]), ("face_paused", "email_code"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])

        # The face scan is closed for now, even for the right person and after logging in again.
        self.assertEqual(post(self.client, "/api/auth/face-scan/").json()["next"], "email_code")
        self.assertEqual(self.password_step().json()["next"], "email_code")
        self.assertEqual(len(mail.outbox), 1)  # no second email within the cooldown

        res = post(self.client, "/api/auth/login-code/verify/", {"code": self.emailed_code()})
        self.assertEqual(res.json(), {"ok": True, "redirect": "/dashboard/"})
        self.assertTrue(self.logged_in())
        self.assertEqual((self.last_record().kind, self.last_record().outcome), (Kind.EMAIL_CODE, Outcome.SUCCESS))
        note = Notification.objects.get(user=self.user, type=NotificationType.SIGN_IN_ALERT)
        self.assertIn("email code instead of a face scan", note.message)

    def test_wrong_codes_and_burning(self):
        self.password_step()
        self.assertEqual(post(self.client, "/api/auth/login-code/send/").status_code, 200)
        code = self.emailed_code()
        self.assertEqual(post(self.client, "/api/auth/login-code/send/").status_code, 429)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.assertEqual(post(self.client, "/api/auth/login-code/verify/", {"code": wrong}).status_code, 400)
        self.assertEqual(post(self.client, "/api/auth/login-code/verify/", {"code": code}).status_code, 400)  # burned
        self.assertFalse(self.logged_in())
        self.assertEqual(VerificationRecord.objects.filter(outcome=Outcome.WRONG_CODE).count(), 5)

    def test_someone_without_a_camera_can_ask_for_the_code(self):
        self.password_step()
        res = post(self.client, "/api/auth/login-code/send/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(post(self.client, "/api/auth/login-code/verify/", {"code": self.emailed_code()}).status_code, 200)
        self.assertTrue(self.logged_in())
        self.assertTrue(Notification.objects.filter(user=self.user, type=NotificationType.SIGN_IN_ALERT).exists())

    def test_reset_codes_and_sign_in_codes_are_not_interchangeable(self):
        post(self.client, "/api/auth/reset/send-code/", {"identifier": self.user.email})
        reset_code = re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)
        self.password_step()
        self.assertEqual(post(self.client, "/api/auth/login-code/verify/", {"code": reset_code}).status_code, 400)
        self.assertFalse(self.logged_in())

        post(self.client, "/api/auth/login-code/send/")
        res = post(self.client, "/api/auth/reset/verify-code/", {"identifier": self.user.email, "code": self.emailed_code()})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(set(PasswordResetCode.objects.values_list("purpose", flat=True)), {"reset", "login"})

    def test_pause_ends_after_the_window(self):
        self.password_step()
        for _ in range(3):
            self.scan(person=BEN)
        self.assertEqual(face_login.attempts_left(self.user), 0)
        VerificationRecord.objects.update(created_at=timezone.now() - timedelta(minutes=16))
        self.assertEqual(self.password_step().json(), {"ok": True, "next": "face_scan", "attempts_left": 3})

    def test_a_successful_scan_clears_earlier_failures(self):
        self.password_step()
        self.scan(person=BEN)
        self.scan(person=BEN)
        self.assertEqual(self.scan().status_code, 200)
        other = Client()
        self.assertEqual(self.password_step(other).json()["attempts_left"], 3)


class AccountsWithoutAFaceTests(FaceLoginCase):
    def test_command_line_admin_skips_the_face_scan_unless_configured_otherwise(self):
        admin = User.objects.create_superuser("admin@reuse.com", PASSWORD, first_name="REUSE", last_name="Administrator")
        res = self.password_step(user=admin)
        self.assertEqual(res.json(), {"ok": True, "redirect": "/admin-dashboard/"})
        with self.settings(FACE_LOGIN_EXEMPT_SUPERUSERS=False):
            self.assertEqual(self.password_step(Client(), user=admin).json()["next"], "enroll_id")

    def test_account_without_a_face_adds_its_id_then_scans(self):
        user = make_user("ben@g.batstate-u.edu.ph", "+639172222222", person=None)
        FakeOCR.reset()
        FakeOCR.lines = lines("REYES, BEN")
        self.assertEqual(self.password_step(user=user).json()["next"], "enroll_id")
        self.assertEqual(post(self.client, "/api/auth/login-code/send/").status_code, 409)  # the ID comes first

        with fake_storage() as files:
            start = post(self.client, "/api/registration/upload-id/", {"side": "front", "content_type": "image/png", "size": 100})
            files[start.json()["upload"]["fields"]["key"]] = picture(person=BEN)
            self.assertEqual(post(self.client, "/api/registration/extract-id/").status_code, 200)
            res = post(self.client, "/api/auth/enroll-id/", {"consent": False})
            self.assertEqual((res.status_code, list(res.json()["errors"])), (400, ["consent"]))
            res = post(self.client, "/api/auth/enroll-id/", {"consent": True})
            self.assertEqual(res.json()["next"], "face_scan")
            self.assertEqual(files, {})  # the ID image is gone once it has been read

        user.refresh_from_db()
        self.assertTrue(face_login.has_usable_template(user))
        self.assertIsNotNone(user.consent_at)
        self.assertEqual(self.scan(person=ANA).json()["code"], "not_confirmed")  # the scan proves the ID is theirs
        self.assertEqual(self.scan(person=BEN).status_code, 200)

    def test_template_from_another_engine_or_key_means_adding_the_id_again(self):
        User.objects.filter(pk=self.user.pk).update(face_model="some-other-engine")
        self.assertEqual(self.password_step().json()["next"], "enroll_id")
        User.objects.filter(pk=self.user.pk).update(face_model="fake-faces")
        with self.settings(FACE_TEMPLATE_KEY="Q2hhbmdlZEtleUNoYW5nZWRLZXlDaGFuZ2VkS2V5ISE="):
            self.assertEqual(self.password_step(Client()).json()["next"], "enroll_id")

    @override_settings(PASSWORD_HASHERS=REAL_HASHERS)
    def test_passwords_use_argon2_and_old_hashes_are_upgraded_at_login(self):
        self.user.set_password(PASSWORD)
        self.assertTrue(self.user.password.startswith("argon2"))
        self.user.password = make_password(PASSWORD, hasher="pbkdf2_sha256")
        self.user.save()
        self.assertEqual(self.password_step().status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.password.startswith("argon2"))


class AdminPagesTests(FaceLoginCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_superuser("admin@reuse.com", PASSWORD, first_name="REUSE", last_name="Administrator")
        self.client.force_login(self.staff)

    def test_user_list_search_and_detail(self):
        make_user("ben@g.batstate-u.edu.ph", "+639172222222", person=None, middle_name="Reyes")
        page = self.client.get("/admin-dashboard/users/").content.decode()
        self.assertIn("Set up", page)
        self.assertIn("Not yet", page)
        self.assertEqual(len(self.client.get("/admin-dashboard/users/?q=reyes").context["users"]), 1)
        self.assertEqual(len(self.client.get("/admin-dashboard/users/?q=ana+cruz").context["users"]), 2)
        self.assertEqual(len(self.client.get("/admin-dashboard/users/?q=nobody").context["users"]), 0)
        VerificationRecord.objects.create(user=self.user, kind=Kind.FACE_LOGIN, outcome=Outcome.NO_MATCH, similarity=0.21)
        detail = self.client.get(f"/admin-dashboard/users/{self.user.pk}/").content.decode()
        self.assertIn("Face did not match", detail)
        self.assertIn("0.21", detail)

    def test_reset_face_login(self):
        self.client.post(f"/admin-dashboard/users/{self.user.pk}/face-reset/")
        self.user.refresh_from_db()
        self.assertEqual((self.user.face_template, self.user.face_model, self.user.face_enrolled_at), (None, "", None))
        self.assertEqual(self.password_step(Client()).json()["next"], "enroll_id")

    def test_add_edit_and_delete_user_with_name_parts(self):
        res = self.client.post("/admin-dashboard/users/new/", {
            "first_name": "Ben", "middle_name": "", "last_name": "Dela Cruz", "suffix": "jr",
            "email": "ben@g.batstate-u.edu.ph", "mobile": "0917 333 3333", "user_type": "faculty",
        })
        new = User.objects.get(email="ben@g.batstate-u.edu.ph")
        self.assertRedirects(res, f"/admin-dashboard/users/{new.pk}/")
        self.assertEqual((new.full_name, new.suffix, new.has_usable_password()), ("Ben Dela Cruz Jr", "Jr", False))
        self.assertIn("school ID", mail.outbox[-1].body)

        res = self.client.post("/admin-dashboard/users/new/", {"first_name": "B3n", "last_name": "N/A", "email": "x@gmail.com", "mobile": "1", "user_type": "x"})
        self.assertEqual((res.status_code, User.objects.filter(email="x@gmail.com").exists()), (200, False))

        self.client.post(f"/admin-dashboard/users/{new.pk}/", {
            "first_name": "Benjamin", "last_name": "Dela Cruz", "email": new.email, "mobile": new.mobile, "user_type": "staff",
        })
        new.refresh_from_db()
        self.assertEqual((new.first_name, new.suffix, new.user_type), ("Benjamin", "", "staff"))

        self.client.post(f"/admin-dashboard/users/{new.pk}/delete/")
        self.assertFalse(User.objects.filter(pk=new.pk).exists())
        self.client.post(f"/admin-dashboard/users/{self.staff.pk}/delete/")
        self.assertTrue(User.objects.filter(pk=self.staff.pk).exists())
