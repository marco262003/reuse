"""Run with:  python manage.py test"""
import json
import re
from datetime import timedelta

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from . import services
from .models import (
    Borrowing, BorrowRequest, Item, ItemSubscription, Notification, NotificationType,
    PasswordResetCode, RequestStatus, User,
)

PASSWORD = "Strong@123"


def post(client, url, data, method="post"):
    return getattr(client, method)(url, data=json.dumps(data), content_type="application/json")


def schedule(hours_from_now=2, length_hours=48):
    start = timezone.localtime(timezone.now() + timedelta(hours=hours_from_now))
    end = start + timedelta(hours=length_hours)
    return {
        "borrow_date": start.strftime("%Y-%m-%d"), "borrow_time": start.strftime("%H:%M"),
        "return_date": end.strftime("%Y-%m-%d"), "return_time": end.strftime("%H:%M"),
    }


def make_user(email="ana@g.batstate-u.edu.ph", mobile="+639171111111", verified=True):
    return User.objects.create_user(
        email=email, password=PASSWORD, full_name="Ana Reyes Cruz", mobile=mobile,
        user_type="student", is_email_verified=verified,
    )


def request_payload(item, quantity=1, **extra):
    data = {"item_id": item.pk, "quantity": quantity, "reason": "class_activity", **schedule()}
    data.update(extra)
    return data


class RegistrationAndLoginTests(TestCase):
    def register(self, **overrides):
        data = {
            "full_name": "Ana Reyes Cruz", "email": "ana@g.batstate-u.edu.ph", "mobile": "0917 111 1111",
            "user_type": "student", "password": PASSWORD, "confirm_password": PASSWORD,
        }
        data.update(overrides)
        return post(self.client, "/api/auth/register/", data)

    def test_register_verify_then_login(self):
        res = self.register()
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["message"], "A verification link has been sent to your school email.")
        user = User.objects.get(email="ana@g.batstate-u.edu.ph")
        self.assertFalse(user.is_email_verified)
        self.assertEqual(user.mobile, "+639171111111")  # stored in one consistent format

        res = post(self.client, "/api/auth/login/", {"email": user.email, "password": PASSWORD})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["message"], "Account not verified. Please verify your email before logging in.")

        link = re.search(r"/verify-email/([^/\s]+)/", mail.outbox[0].body).group(1)
        res = self.client.get(f"/verify-email/{link}/")
        self.assertEqual(res.status_code, 302)
        self.assertTrue(Notification.objects.filter(user=user, type=NotificationType.ACCOUNT_VERIFIED).exists())

        res = post(self.client, "/api/auth/login/", {"email": user.email, "password": PASSWORD})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["redirect"], "/dashboard/")

    def test_login_messages(self):
        make_user()
        res = post(self.client, "/api/auth/login/", {"email": "nobody@g.batstate-u.edu.ph", "password": "x"})
        self.assertEqual(res.json()["message"], "Email is not registered.")
        res = post(self.client, "/api/auth/login/", {"email": "ana@g.batstate-u.edu.ph", "password": "wrong"})
        self.assertEqual(res.json()["message"], "Email or password is incorrect.")
        res = post(self.client, "/api/auth/login/", {"email": "ana@gmail.com", "password": "x"})
        self.assertIn("email", res.json()["errors"])

    def test_registration_validation(self):
        res = self.register(email="ana@gmail.com", full_name="Ana 123", password="weak",
                            confirm_password="other", mobile="12345", user_type="")
        errors = res.json()["errors"]
        for field in ("email", "full_name", "password", "confirm_password", "mobile", "user_type"):
            self.assertIn(field, errors)
        self.assertEqual(User.objects.count(), 0)

    def test_accented_and_hyphenated_names_are_accepted(self):
        res = self.register(full_name="Niño Dela Peña-O'Brien")
        self.assertEqual(res.status_code, 201)

    def test_duplicate_email_is_rejected(self):
        make_user()
        res = self.register()
        self.assertEqual(res.status_code, 400)
        self.assertIn("email", res.json()["errors"])
        self.assertEqual(User.objects.count(), 1)

    def test_deactivated_account_cannot_log_in(self):
        user = make_user()
        user.is_active = False
        user.save()
        res = post(self.client, "/api/auth/login/", {"email": user.email, "password": PASSWORD})
        self.assertEqual(res.status_code, 403)


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_full_reset_flow_with_cooldown(self):
        res = post(self.client, "/api/auth/reset/send-code/", {"identifier": self.user.email})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["cooldown"], 60)
        code = re.search(r"code is (\d{6})", mail.outbox[0].body).group(1)

        res = post(self.client, "/api/auth/reset/send-code/", {"identifier": self.user.email})
        self.assertEqual(res.status_code, 429)  # 60-second cooldown enforced by the server

        res = post(self.client, "/api/auth/reset/change-password/", {"password": "New@12345", "confirm_password": "New@12345"})
        self.assertEqual(res.status_code, 403)  # cannot skip code verification

        res = post(self.client, "/api/auth/reset/verify-code/", {"identifier": self.user.email, "code": "000000"})
        self.assertEqual(res.json()["message"], "Invalid or expired verification code.")

        res = post(self.client, "/api/auth/reset/verify-code/", {"identifier": self.user.email, "code": code})
        self.assertEqual(res.status_code, 200)

        res = post(self.client, "/api/auth/reset/change-password/", {"password": "New@12345", "confirm_password": "New@12345"})
        self.assertEqual(res.json()["message"], "Your password has been changed successfully.")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("New@12345"))

    def test_unknown_contact_and_expired_code(self):
        res = post(self.client, "/api/auth/reset/send-code/", {"identifier": "nobody@g.batstate-u.edu.ph"})
        self.assertEqual(res.json()["message"], "Email or mobile number is not registered.")

        post(self.client, "/api/auth/reset/send-code/", {"identifier": self.user.email})
        code = re.search(r"code is (\d{6})", mail.outbox[0].body).group(1)
        PasswordResetCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        res = post(self.client, "/api/auth/reset/verify-code/", {"identifier": self.user.email, "code": code})
        self.assertEqual(res.json()["message"], "Invalid or expired verification code.")

    def test_code_is_burned_after_too_many_wrong_attempts(self):
        post(self.client, "/api/auth/reset/send-code/", {"identifier": self.user.email})
        code = re.search(r"code is (\d{6})", mail.outbox[0].body).group(1)
        for _ in range(5):
            post(self.client, "/api/auth/reset/verify-code/", {"identifier": self.user.email, "code": "111111"})
        res = post(self.client, "/api/auth/reset/verify-code/", {"identifier": self.user.email, "code": code})
        self.assertEqual(res.status_code, 400)


class InventoryFlowTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.other = make_user("ben@g.batstate-u.edu.ph", "+639172222222")
        self.admin = User.objects.create_superuser("admin@reuse.local", "Admin@12345", full_name="Admin User")
        self.item = Item.objects.create(name="Projector", category="av", total_qty=3)
        self.client.force_login(self.user)

    def submit(self, quantity=1, **extra):
        return post(self.client, "/api/requests/", request_payload(self.item, quantity, **extra))

    def test_request_is_validated_against_availability(self):
        res = self.submit(quantity=5)
        self.assertEqual(res.status_code, 409)
        res = self.submit(quantity=2)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["request"]["id"], "R00001")
        self.assertEqual(res.json()["request"]["status"], "pending")

    def test_form_validation(self):
        res = self.submit(reason="other", reason_other="")
        self.assertIn("reason_other", res.json()["errors"])
        res = self.submit(borrow_date="2020-01-01")
        self.assertIn("borrow_date", res.json()["errors"])
        data = request_payload(self.item)
        data["return_date"], data["return_time"] = data["borrow_date"], data["borrow_time"]
        res = post(self.client, "/api/requests/", data)
        self.assertIn("return_date", res.json()["errors"])

    def test_full_lifecycle_and_inventory_counts(self):
        req = BorrowRequest.objects.get(request_id=self.submit(quantity=2).json()["request"]["id"])

        with self.captureOnCommitCallbacks(execute=True):
            services.approve_request(req.pk, self.admin)
        self.item.refresh_from_db()
        self.assertEqual((self.item.reserved_qty, self.item.available_qty), (2, 1))
        req.refresh_from_db()
        self.assertEqual(req.approval_id, "A00001")

        borrowing = services.confirm_pickup(req.pk, self.admin)
        self.assertEqual(borrowing.borrowing_id, "B00001")
        self.item.refresh_from_db()
        self.assertEqual((self.item.reserved_qty, self.item.borrowed_qty, self.item.times_borrowed), (0, 2, 1))

        res = self.client.get("/api/returns/")
        self.assertEqual(len(res.json()["returns"]), 1)

        with self.captureOnCommitCallbacks(execute=True):
            services.confirm_return(borrowing.pk, self.admin)
        self.item.refresh_from_db()
        self.assertEqual((self.item.borrowed_qty, self.item.available_qty), (0, 3))
        self.assertTrue(Notification.objects.filter(user=self.user, type=NotificationType.RETURN_CONFIRMED).exists())
        self.assertEqual(self.client.get("/api/returns/").json()["returns"], [])

    def test_cannot_reserve_more_than_available(self):
        first = BorrowRequest.objects.get(request_id=self.submit(quantity=3).json()["request"]["id"])
        self.client.force_login(self.other)
        # A second request for 3 is allowed (still available at submit time) but approval must fail.
        second = BorrowRequest.objects.get(
            request_id=post(self.client, "/api/requests/", request_payload(self.item, 3)).json()["request"]["id"]
        )
        services.approve_request(first.pk, self.admin)
        with self.assertRaises(services.ServiceError):
            services.approve_request(second.pk, self.admin)
        self.item.refresh_from_db()
        self.assertEqual(self.item.reserved_qty, 3)

    def test_edit_and_cancel_rules(self):
        rid = self.submit(quantity=1).json()["request"]["id"]
        res = post(self.client, f"/api/requests/{rid}/", request_payload(self.item, 2), method="patch")
        self.assertEqual(res.json()["request"]["quantity"], 2)

        req = BorrowRequest.objects.get(request_id=rid)
        services.approve_request(req.pk, self.admin)
        res = post(self.client, f"/api/requests/{rid}/", request_payload(self.item, 1), method="patch")
        self.assertEqual(res.status_code, 409)  # approved reservations cannot be edited

        res = post(self.client, f"/api/requests/{rid}/cancel/", {})
        self.assertEqual(res.json()["request"]["status"], "canceled")
        self.item.refresh_from_db()
        self.assertEqual(self.item.reserved_qty, 0)  # released automatically
        self.assertTrue(Notification.objects.filter(type=NotificationType.RESERVATION_CANCELED).exists())

    def test_ids_are_never_reused(self):
        first = self.submit(quantity=1).json()["request"]["id"]
        post(self.client, f"/api/requests/{first}/cancel/", {})
        second = self.submit(quantity=1).json()["request"]["id"]
        self.assertNotEqual(first, second)

    def test_users_cannot_see_each_others_records(self):
        rid = self.submit().json()["request"]["id"]
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/api/requests/{rid}/").status_code, 404)
        self.assertEqual(post(self.client, f"/api/requests/{rid}/cancel/", {}).status_code, 404)
        self.assertEqual(self.client.get(f"/api/receipts/{rid}/").status_code, 404)
        self.assertEqual(self.client.get("/api/requests/").json()["requests"], [])

    def test_anonymous_requests_are_rejected(self):
        self.client.logout()
        self.assertEqual(self.client.get("/api/items/").status_code, 401)
        self.assertEqual(self.client.get("/dashboard/").status_code, 302)

    def test_search_and_category_filter_work_together(self):
        Item.objects.create(name="Projector Screen", category="av", total_qty=1)
        Item.objects.create(name="Projector Lamp", category="tools", total_qty=1)
        res = self.client.get("/api/items/?q=proj&category=av")
        names = sorted(i["name"] for i in res.json()["items"])
        self.assertEqual(names, ["Projector", "Projector Screen"])


class AvailabilityNotificationTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.admin = User.objects.create_superuser("admin@reuse.local", "Admin@12345", full_name="Admin User")
        self.item = Item.objects.create(name="Wireless Presenter", category="av", total_qty=1, repair_qty=1)
        self.client.force_login(self.user)

    def test_only_subscribers_are_notified_once(self):
        stranger = make_user("zed@g.batstate-u.edu.ph", "+639173333333")
        res = post(self.client, f"/api/items/{self.item.pk}/subscribe/", {})
        self.assertEqual(res.status_code, 200)

        with self.captureOnCommitCallbacks(execute=True):
            self.item.repair_qty = 0
            self.item.save()
            services.item_stock_changed(self.item, 0)
            services.item_stock_changed(self.item, 0)  # a second trigger must not duplicate

        notes = Notification.objects.filter(type=NotificationType.ITEM_AVAILABLE)
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes.first().user, self.user)
        self.assertEqual(notes.first().message, "Wireless Presenter is now available!")
        self.assertFalse(Notification.objects.filter(user=stranger).exists())
        self.assertTrue(ItemSubscription.objects.get(user=self.user).notified_at)

    def test_cannot_subscribe_to_an_available_item(self):
        self.item.repair_qty = 0
        self.item.save()
        res = post(self.client, f"/api/items/{self.item.pk}/subscribe/", {})
        self.assertEqual(res.status_code, 409)


class NotificationApiTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        for i in range(3):
            Notification.objects.create(user=self.user, type="item_available", message=f"n{i}")

    def test_mark_one_and_all_read_newest_first(self):
        data = self.client.get("/api/notifications/").json()["notifications"]
        self.assertEqual([n["message"] for n in data], ["n2", "n1", "n0"])
        post(self.client, f"/api/notifications/{data[0]['id']}/read/", {})
        self.assertEqual(self.client.get("/api/me/").json()["unread_count"], 2)
        post(self.client, "/api/notifications/read-all/", {})
        self.assertEqual(self.client.get("/api/me/").json()["unread_count"], 0)


class DueNotificationCommandTests(TestCase):
    def test_due_soon_and_overdue_created_once(self):
        from django.core.management import call_command

        user = make_user()
        admin = User.objects.create_superuser("admin@reuse.local", "Admin@12345", full_name="Admin User")
        item = Item.objects.create(name="Projector", category="av", total_qty=2)
        for hours in (3, 5):
            req = BorrowRequest.objects.create(
                request_id=f"R9000{hours}", user=user, item=item, quantity=1,
                borrow_at=timezone.now(), return_at=timezone.now() + timedelta(hours=hours),
                reason="class_activity", status=RequestStatus.APPROVED,
            )
            item.reserved_qty += 1
            item.save()
            services.confirm_pickup(req.pk, admin)
        Borrowing.objects.filter(request__request_id="R90003").update(due_at=timezone.now() - timedelta(hours=1))

        call_command("process_due_notifications")
        call_command("process_due_notifications")  # running twice must not duplicate
        self.assertEqual(Notification.objects.filter(type=NotificationType.OVERDUE).count(), 1)
        self.assertEqual(Notification.objects.filter(type=NotificationType.DUE_SOON).count(), 1)


class CustomAdminPagesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="admin@reuse.com", password="Admin@1234", full_name="REUSE Administrator",
            user_type="staff",
        )
        self.client.force_login(self.admin)

    def test_custom_admin_pages_render(self):
        for url in (
            "/admin-dashboard/", "/admin-dashboard/items/", "/admin-dashboard/requests/", "/admin-dashboard/borrowings/", "/admin-dashboard/returns/",
            "/admin-dashboard/users/", "/admin-dashboard/notifications/",
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_admin_can_add_and_archive_inventory_item(self):
        response = self.client.post("/admin-dashboard/items/save/", {
            "name": "Test Projector", "category": "av", "condition": "good",
            "total_qty": "3", "repair_qty": "0", "description": "Testing",
            "image_url": "",
        })
        self.assertEqual(response.status_code, 302)
        item = Item.objects.get(name="Test Projector")
        self.assertEqual(item.total_qty, 3)
        response = self.client.post(f"/admin-dashboard/items/{item.pk}/toggle/")
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertFalse(item.is_active)

    def test_staff_login_redirects_to_custom_dashboard(self):
        self.client.logout()
        response = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"email": "admin@reuse.com", "password": "Admin@1234"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["redirect"], "/admin-dashboard/")
