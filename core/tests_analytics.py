"""Analytics (forecast, return times, overdue risk) and the browse / dashboard hints.

Run with:  python manage.py test core.tests_analytics
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from . import analytics
from .models import (
    Borrowing, BorrowingStatus, BorrowRequest, Item, RequestStatus, User, next_public_id,
)

PASSWORD = "Strong@123"
NOW = timezone.now()


def make_user(n=1):
    return User.objects.create_user(
        email=f"user{n}@g.batstate-u.edu.ph", password=PASSWORD, first_name=f"User{n}", last_name="Test",
        mobile=f"+63917000{n:04d}", user_type="student", is_email_verified=True,
    )


def borrow(user, item, picked, due, returned=None, qty=1):
    """A completed pickup (and optionally its return), written straight to the tables."""
    req = BorrowRequest.objects.create(
        request_id=next_public_id("R"), user=user, item=item, quantity=qty,
        borrow_at=picked, return_at=due, reason="class_activity", status=RequestStatus.FULFILLED,
    )
    return Borrowing.objects.create(
        borrowing_id=next_public_id("B"), request=req, user=user, item=item, quantity=qty,
        picked_up_at=picked, due_at=due, returned_at=returned,
        status=BorrowingStatus.RETURNED if returned else BorrowingStatus.ACTIVE,
    )


class ForecastMathTests(TestCase):
    def test_flat_history_forecasts_the_same_level(self):
        forecast, low, high = analytics.holt_forecast([10] * 8)
        self.assertEqual(len(forecast), 4)
        for f, lo, hi in zip(forecast, low, high):
            self.assertAlmostEqual(f, 10, delta=0.5)
            self.assertLessEqual(lo, f)
            self.assertGreaterEqual(hi, f)

    def test_rising_history_keeps_rising_but_damped(self):
        forecast, _, _ = analytics.holt_forecast([2, 4, 6, 8, 10, 12, 14, 16])
        self.assertGreater(forecast[0], 16)
        steps = [b - a for a, b in zip(forecast, forecast[1:])]
        self.assertTrue(all(s > 0 for s in steps))
        self.assertTrue(all(b < a for a, b in zip(steps, steps[1:])), "the trend should flatten further out")

    def test_falling_history_never_goes_negative_and_band_widens(self):
        forecast, low, high = analytics.holt_forecast([12, 9, 6, 3, 1, 0])
        self.assertTrue(all(v >= 0 for v in forecast + low))
        widths = [h - lo for lo, h in zip(low, high)]
        self.assertLessEqual(widths[0], widths[-1])

    def test_days_late_rounds_up(self):
        due = NOW
        self.assertEqual(analytics.days_late(due - timedelta(hours=1), due), 0)
        self.assertEqual(analytics.days_late(due, due), 0)
        self.assertEqual(analytics.days_late(due + timedelta(hours=2), due), 1)
        self.assertEqual(analytics.days_late(due + timedelta(days=2, minutes=1), due), 3)


class TrendAndOutlookTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.item = Item.objects.create(name="Projector", category="av", total_qty=3)

    def test_not_enough_history_explains_why(self):
        borrow(self.user, self.item, NOW - timedelta(days=1), NOW + timedelta(days=1))
        trend = analytics.demand_trend(NOW)
        self.assertFalse(trend["has_forecast"])
        self.assertEqual(trend["forecast"], [])
        self.assertIn("needs at least", trend["summary"])

    def test_forecast_after_enough_weeks(self):
        for week in range(8):
            for _ in range(3):
                picked = NOW - timedelta(weeks=week, hours=1)
                borrow(self.user, self.item, picked, picked + timedelta(days=1), returned=picked + timedelta(hours=20))
        trend = analytics.demand_trend(NOW)
        self.assertTrue(trend["has_forecast"])
        self.assertEqual(len(trend["forecast"]), 4)
        # Complete weeks are charted; the current week's pickups are reported separately.
        self.assertEqual(sum(p["value"] for p in trend["points"]) + trend["this_week"], 24)
        self.assertEqual(trend["forecast"][0]["label"], analytics.week_start(NOW).strftime("%b %d"))
        self.assertIn("Based on", trend["summary"])

    def test_current_week_is_not_charted_as_a_drop(self):
        for week in range(1, 7):
            for _ in range(5):
                picked = analytics.week_start(NOW) - timedelta(weeks=week) + timedelta(days=1)
                borrow(self.user, self.item, picked, picked + timedelta(days=1), returned=picked + timedelta(hours=5))
        borrow(self.user, self.item, NOW, NOW + timedelta(days=1))  # only one so far this week
        trend = analytics.demand_trend(NOW)
        self.assertEqual([p["value"] for p in trend["points"]], [5] * 6)
        self.assertEqual(trend["this_week"], 1)
        self.assertAlmostEqual(trend["forecast"][0]["value"], 5, delta=0.5)

    def test_window_starts_at_first_pickup(self):
        picked = analytics.week_start(NOW) - timedelta(weeks=2) + timedelta(hours=3)
        borrow(self.user, self.item, picked, picked + timedelta(days=1))
        self.assertEqual(analytics.demand_trend(NOW)["weeks"], 2)

    def test_stock_outlook_flags_item_kept_longer_than_stock_allows(self):
        # 3 units borrowed every week and kept ~2 weeks => ~6 units out at once, only 3 exist.
        busy = Item.objects.create(name="Basketball", category="sports", total_qty=3)
        for week in range(6):
            for _ in range(3):
                picked = NOW - timedelta(weeks=week, hours=2)
                borrow(self.user, busy, picked, picked + timedelta(days=14), returned=picked + timedelta(days=14))
        outlook = analytics.stock_outlook(NOW)
        self.assertEqual([o["item"] for o in outlook], [busy])
        self.assertEqual(outlook[0]["level"], "short")
        self.assertGreater(outlook[0]["shortfall"], 0)

    def test_top_items_ranked_by_times_borrowed(self):
        other = Item.objects.create(name="HDMI Cable", category="av", total_qty=10)
        for _ in range(3):
            borrow(self.user, other, NOW - timedelta(days=2), NOW + timedelta(days=1))
        borrow(self.user, self.item, NOW - timedelta(days=2), NOW + timedelta(days=1), qty=2)
        top = analytics.top_items(now=NOW)
        self.assertEqual([(t["name"], t["times"], t["pct"]) for t in top], [("HDMI Cable", 3, 100), ("Projector", 1, 33)])
        self.assertEqual(top[1]["units"], 2)


class ReturnTimesAndRiskTests(TestCase):
    def setUp(self):
        self.item = Item.objects.create(name="Projector", category="av", total_qty=20)

    def test_histogram_buckets_and_on_time_rate(self):
        user = make_user()
        due = NOW - timedelta(days=20)
        for late_by in (timedelta(hours=-2), timedelta(0), timedelta(hours=5), timedelta(days=2, hours=1), timedelta(days=10)):
            borrow(user, self.item, due - timedelta(days=1), due, returned=due + late_by)
        borrow(user, self.item, NOW - timedelta(days=3), NOW - timedelta(days=1))  # still out and overdue
        result = analytics.return_histogram(now=NOW)
        self.assertEqual([b["count"] for b in result["buckets"]], [2, 1, 1, 0, 1])
        self.assertEqual(result["on_time_rate"], 40)
        self.assertEqual(result["still_overdue"], 1)

    def test_new_borrower_is_low_risk_and_habitual_late_borrower_is_flagged(self):
        newcomer, habitual = make_user(1), make_user(2)
        for i in range(3):
            due = NOW - timedelta(days=10 + i)
            borrow(habitual, self.item, due - timedelta(days=1), due, returned=due + timedelta(days=2))
        borrow(newcomer, self.item, NOW, NOW + timedelta(days=1))
        flagged_borrowing = borrow(habitual, self.item, NOW, NOW + timedelta(days=1))

        self.assertEqual(analytics.risk_level(analytics.risk_score(0, 0, False)), "low")
        risk = analytics.overdue_risk(now=NOW)
        self.assertEqual([r["borrowing"] for r in risk], [flagged_borrowing])
        self.assertEqual(risk[0]["level"], "high")
        self.assertIn("Returned 3 of 3 items late", risk[0]["reason"])

    def test_one_late_return_alone_is_not_high_risk(self):
        user = make_user()
        due = NOW - timedelta(days=5)
        borrow(user, self.item, due - timedelta(days=1), due, returned=due + timedelta(hours=3))
        self.assertNotEqual(analytics.risk_level(analytics.risk_score(1, 1, False)), "high")

    def test_already_overdue_borrowings_are_not_repeated_in_risk(self):
        user = make_user()
        for i in range(3):
            due = NOW - timedelta(days=10 + i)
            borrow(user, self.item, due - timedelta(days=1), due, returned=due + timedelta(days=2))
        overdue = borrow(user, self.item, NOW - timedelta(days=3), NOW - timedelta(hours=1))
        upcoming = borrow(user, self.item, NOW, NOW + timedelta(days=2))
        risk = analytics.overdue_risk(now=NOW)
        self.assertNotIn(overdue, [r["borrowing"] for r in risk])
        self.assertIn("has another item overdue now", risk[0]["reason"])
        self.assertEqual(risk[0]["borrowing"], upcoming)


class UserHintsTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_unavailable_item_shows_soonest_return(self):
        item = Item.objects.create(name="Projector", category="av", total_qty=2, borrowed_qty=2)
        borrow(self.user, item, NOW, NOW + timedelta(days=3))
        borrow(self.user, item, NOW, NOW + timedelta(days=1))
        repair = Item.objects.create(name="Presenter", category="av", total_qty=1, repair_qty=1)
        free = Item.objects.create(name="Cable", category="av", total_qty=1)
        data = {i["name"]: i for i in self.client.get("/api/items/").json()["items"]}
        expected = timezone.localtime(NOW + timedelta(days=1)).isoformat()
        self.assertEqual(data["Projector"]["next_available_at"], expected)
        self.assertIsNone(data["Presenter"]["next_available_at"])
        self.assertIsNone(data["Cable"]["next_available_at"])
        detail = self.client.get(f"/api/items/{item.pk}/").json()["item"]
        self.assertEqual(detail["next_available_at"], expected)
        self.assertTrue(repair and free)

    def test_sort_and_available_filter(self):
        Item.objects.create(name="Alpha", category="av", total_qty=1, repair_qty=1, times_borrowed=1)
        Item.objects.create(name="Bravo", category="av", total_qty=1, times_borrowed=9)
        Item.objects.create(name="Charlie", category="av", total_qty=1, times_borrowed=4)
        names = lambda q: [i["name"] for i in self.client.get(f"/api/items/{q}").json()["items"]]
        self.assertEqual(names(""), ["Bravo", "Charlie", "Alpha"])          # available first
        self.assertEqual(names("?sort=name"), ["Alpha", "Bravo", "Charlie"])
        self.assertEqual(names("?sort=popular"), ["Bravo", "Charlie", "Alpha"])
        self.assertEqual(names("?available=1&sort=name"), ["Bravo", "Charlie"])
        self.assertEqual(names("?sort=bogus"), ["Bravo", "Charlie", "Alpha"])

    def test_dashboard_reports_return_record_and_first_visit(self):
        res = self.client.get("/api/dashboard/").json()
        self.assertEqual(res["record"], {"returned": 0, "on_time": 0, "on_time_rate": None})
        self.assertFalse(res["has_any_request"])
        item = Item.objects.create(name="Projector", category="av", total_qty=5)
        due = NOW - timedelta(days=3)
        borrow(self.user, item, due - timedelta(days=1), due, returned=due - timedelta(hours=1))
        borrow(self.user, item, due - timedelta(days=1), due, returned=due + timedelta(days=1))
        res = self.client.get("/api/dashboard/").json()
        self.assertEqual(res["record"], {"returned": 2, "on_time": 1, "on_time_rate": 50})
        self.assertTrue(res["has_any_request"])


class AnalyticsPageTests(TestCase):
    def test_staff_sees_page_and_others_are_sent_away(self):
        user = make_user()
        item = Item.objects.create(name="Projector", category="av", total_qty=3)
        for week in range(6):
            picked = NOW - timedelta(weeks=week, hours=1)
            borrow(user, item, picked, picked + timedelta(days=1), returned=picked + timedelta(days=2))
        self.client.force_login(user)
        self.assertEqual(self.client.get("/admin-dashboard/analytics/").status_code, 302)

        admin = User.objects.create_superuser("admin@reuse.local", "Admin@12345", first_name="Admin", last_name="User")
        self.client.force_login(admin)
        res = self.client.get("/admin-dashboard/analytics/")
        self.assertEqual(res.status_code, 200)
        for text in ("Weekly Pickups and Forecast", "Stock Outlook", "Overdue Risk", "Most Borrowed Items", "Return Times", "data-forecast"):
            self.assertContains(res, text)
        dash = self.client.get("/admin-dashboard/")
        self.assertEqual(dash.status_code, 200)
        self.assertContains(dash, "Open analytics")
        self.assertEqual(dash.context["borrowed_units"], 0)
        self.assertEqual(dash.context["available_units"], 3)
