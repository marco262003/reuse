"""Numbers behind the admin Analytics page and a few user-facing hints.

Everything here is plain Python over the existing tables (no extra libraries), so it
runs the same on PostgreSQL and SQLite and is easy to explain:

* Demand forecast: Holt's linear method with a damped trend, fitted to weekly pickups.
  The band is the typical one-week-ahead error, widened for weeks further out.
* Stock-out warning: Little's law. Units out at once ~= units borrowed per week x
  average loan length in weeks. If that exceeds the units that can be lent, the item
  will run short.
* Overdue risk: each borrower's past late-return rate, smoothed so one late return by a
  new user does not label them "high risk".
"""
import math
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Min, Sum
from django.utils import timezone

from .models import Borrowing, BorrowingStatus, BorrowRequest, Item, RequestStatus

HISTORY_WEEKS = 12
HORIZON_WEEKS = 4
MIN_WEEKS_FOR_FORECAST = 4
MIN_PICKUPS_FOR_FORECAST = 5


# ---------------------------------------------------------------------------
# Weeks
# ---------------------------------------------------------------------------
def week_start(dt):
    """Monday 00:00 (local time) of the week that contains dt."""
    local = timezone.localtime(dt)
    monday = local - timedelta(days=local.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def weekly_window(now=None, weeks=HISTORY_WEEKS):
    """The Mondays of the last `weeks` complete weeks, oldest first.

    The current week is left out because it is not over yet; counting it would look
    like a sudden drop. The window also never starts before the first-ever pickup, so
    a new system is not forecast from a run of empty weeks before it went live.
    """
    now = now or timezone.now()
    current = week_start(now)
    starts = [current - timedelta(weeks=i) for i in range(weeks, 0, -1)]
    first = Borrowing.objects.aggregate(first=Min("picked_up_at"))["first"]
    if first:
        first_week = week_start(first)
        starts = [s for s in starts if s >= first_week]
    return starts


def weekly_counts(starts, queryset=None, field="picked_up_at", value=None):
    """Count rows per week (or sum `value`) for the given week starts."""
    queryset = queryset if queryset is not None else Borrowing.objects.all()
    rows = queryset.filter(**{f"{field}__gte": starts[0]}).values_list(field, value or "id")
    index = {s: i for i, s in enumerate(starts)}
    totals = [0] * len(starts)
    for when, amount in rows:
        i = index.get(week_start(when))
        if i is not None:
            totals[i] += amount if value else 1
    return totals


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------
def holt_forecast(series, horizon=HORIZON_WEEKS, alpha=0.5, beta=0.3, phi=0.9):
    """Damped-trend Holt forecast. Returns (forecast, low, high) lists of length horizon.

    alpha: how fast the level follows new weeks. beta: how fast the trend follows.
    phi < 1 flattens the trend further out so a short rise is not projected forever.
    """
    if not series:
        return [0.0] * horizon, [0.0] * horizon, [0.0] * horizon
    if len(series) == 1:
        level, trend = float(series[0]), 0.0
        errors = []
    else:
        level, trend = float(series[0]), float(series[1] - series[0])
        errors = []
        for x in series[1:]:
            expected = level + phi * trend
            errors.append(x - expected)
            previous = level
            level = alpha * x + (1 - alpha) * expected
            trend = beta * (level - previous) + (1 - beta) * phi * trend
    spread = math.sqrt(sum(e * e for e in errors) / len(errors)) if errors else max(1.0, level * 0.5)

    forecast, low, high = [], [], []
    damp = 0.0
    for h in range(1, horizon + 1):
        damp += phi ** h
        point = max(0.0, level + damp * trend)
        margin = 1.28 * spread * math.sqrt(h)  # roughly an 80% band
        forecast.append(round(point, 1))
        low.append(round(max(0.0, point - margin), 1))
        high.append(round(point + margin, 1))
    return forecast, low, high


def demand_trend(now=None):
    """Weekly pickups for the complete weeks in the window plus a 4-week forecast.

    The forecast's first week is the current one, so `this_week` (pickups so far) can be
    read against it.
    """
    now = now or timezone.now()
    current = week_start(now)
    starts = weekly_window(now)
    counts = weekly_counts(starts) if starts else []
    this_week = Borrowing.objects.filter(picked_up_at__gte=current).count()
    points = [{"label": s.strftime("%b %d"), "value": c} for s, c in zip(starts, counts)]

    enough = len(starts) >= MIN_WEEKS_FOR_FORECAST and sum(counts) >= MIN_PICKUPS_FOR_FORECAST
    result = {
        "points": points,
        "weeks": len(starts),
        "total": sum(counts) + this_week,
        "this_week": this_week,
        "has_chart": any(counts),
        "has_forecast": enough,
        "forecast": [],
        "summary": "",
    }
    if not enough:
        result["summary"] = (
            f"A forecast needs at least {MIN_WEEKS_FOR_FORECAST} complete weeks and {MIN_PICKUPS_FOR_FORECAST} pickups of history. "
            f"So far: {len(starts)} complete week{'s' if len(starts) != 1 else ''}, {sum(counts)} pickup{'s' if sum(counts) != 1 else ''}"
            f" (plus {this_week} this week)."
        )
        return result

    forecast, low, high = holt_forecast(counts)
    result["forecast"] = [
        {"label": (current + timedelta(weeks=h)).strftime("%b %d"), "value": f, "low": lo, "high": hi}
        for h, (f, lo, hi) in enumerate(zip(forecast, low, high))
    ]
    recent = sum(counts[-4:]) / min(4, len(counts))
    upcoming = sum(forecast) / len(forecast)
    if recent == 0:
        change = "up from none"
    else:
        pct = round((upcoming - recent) / recent * 100)
        change = "about the same as" if abs(pct) < 10 else (f"{pct}% higher than" if pct > 0 else f"{-pct}% lower than")
        change = f"{change} the last 4 weeks"
    result["summary"] = (
        f"Expect about {upcoming:.0f} pickups a week over the next {HORIZON_WEEKS} weeks, {change}. "
        f"This week so far: {this_week} (forecast about {forecast[0]:.0f}). "
        f"Based on {len(starts)} complete weeks of data."
    )
    return result


def stock_outlook(now=None, limit=6):
    """Items likely to run short soon, using units borrowed per week x average loan length."""
    now = now or timezone.now()
    starts = weekly_window(now)
    if len(starts) < MIN_WEEKS_FOR_FORECAST:
        return []
    since = starts[0]
    loans = defaultdict(list)
    for item_id, qty, picked, due, returned in Borrowing.objects.filter(picked_up_at__gte=since).values_list(
        "item_id", "quantity", "picked_up_at", "due_at", "returned_at",
    ):
        loans[item_id].append((qty, picked, due, returned))

    items = {i.pk: i for i in Item.objects.filter(pk__in=loans, is_active=True)}
    index = {s: i for i, s in enumerate(starts)}
    outlook = []
    for item_id, rows in loans.items():
        item = items.get(item_id)
        if not item or len(rows) < 3:
            continue
        weekly = [0] * len(starts)
        days = []
        for qty, picked, due, returned in rows:
            i = index.get(week_start(picked))
            if i is not None:
                weekly[i] += qty
            end = returned or max(due, now)
            days.append(max((end - picked).total_seconds() / 86400, 0.25))
        forecast, _, high = holt_forecast(weekly)
        per_week = sum(forecast) / len(forecast)
        loan_weeks = (sum(days) / len(days)) / 7
        projected = per_week * loan_weeks          # units out at the same time (Little's law)
        usable = max(item.total_qty - item.repair_qty, 0)
        if usable == 0:
            continue
        load = projected / usable
        if load < 0.8:
            continue
        outlook.append({
            "item": item,
            "per_week": round(per_week, 1),
            "loan_days": round(loan_weeks * 7, 1),
            "projected": math.ceil(projected),
            "usable": usable,
            "load_pct": min(round(load * 100), 999),
            "level": "short" if load >= 1 else "tight",
            "shortfall": max(math.ceil(projected) - usable, 0),
        })
    outlook.sort(key=lambda o: o["load_pct"], reverse=True)
    return outlook[:limit]


# ---------------------------------------------------------------------------
# Top items, return times
# ---------------------------------------------------------------------------
def top_items(days=90, limit=10, now=None):
    now = now or timezone.now()
    rows = list(
        Borrowing.objects.filter(picked_up_at__gte=now - timedelta(days=days))
        .values("item_id", "item__name", "item__category")
        .annotate(times=Count("id"), units=Sum("quantity"))
        .order_by("-times", "-units", "item__name")[:limit]
    )
    peak = max((r["times"] for r in rows), default=0)
    return [
        {"name": r["item__name"], "times": r["times"], "units": r["units"],
         "pct": round(r["times"] / peak * 100) if peak else 0}
        for r in rows
    ]


RETURN_BUCKETS = [
    ("On time", "ontime", 0, 0),
    ("1 day late", "late1", 1, 1),
    ("2–3 days late", "late2", 2, 3),
    ("4–7 days late", "late3", 4, 7),
    ("Over a week late", "late4", 8, None),
]


def days_late(returned_at, due_at):
    """0 when returned by the due time, otherwise whole days late (rounded up)."""
    if returned_at <= due_at:
        return 0
    return math.ceil((returned_at - due_at).total_seconds() / 86400)


def return_histogram(days=180, now=None):
    now = now or timezone.now()
    rows = Borrowing.objects.filter(
        status=BorrowingStatus.RETURNED, returned_at__gte=now - timedelta(days=days),
    ).values_list("returned_at", "due_at")
    counts = [0] * len(RETURN_BUCKETS)
    for returned_at, due_at in rows:
        late = days_late(returned_at, due_at)
        for i, (_, _, lo, hi) in enumerate(RETURN_BUCKETS):
            if late >= lo and (hi is None or late <= hi):
                counts[i] += 1
                break
    total = sum(counts)
    peak = max(counts) if counts else 0
    still_out = Borrowing.objects.filter(status=BorrowingStatus.ACTIVE, due_at__lt=now).count()
    return {
        "buckets": [
            {"label": label, "key": key, "count": c, "pct": round(c / peak * 100) if peak else 0,
             "share": round(c / total * 100) if total else 0}
            for (label, key, _, _), c in zip(RETURN_BUCKETS, counts)
        ],
        "total": total,
        "on_time_rate": round(counts[0] / total * 100) if total else None,
        "still_overdue": still_out,
    }


# ---------------------------------------------------------------------------
# Overdue risk
# ---------------------------------------------------------------------------
PRIOR_LATE, PRIOR_WEIGHT = 0.3, 2  # a new borrower starts at a 15% late rate


def user_return_records(user_ids=None):
    """{user_id: (returned_count, late_count)} from returned borrowings."""
    qs = Borrowing.objects.filter(status=BorrowingStatus.RETURNED)
    if user_ids is not None:
        qs = qs.filter(user_id__in=user_ids)
    records = defaultdict(lambda: [0, 0])
    for user_id, returned_at, due_at in qs.values_list("user_id", "returned_at", "due_at"):
        records[user_id][0] += 1
        if returned_at > due_at:
            records[user_id][1] += 1
    return {k: tuple(v) for k, v in records.items()}


def risk_score(returned, late, has_overdue):
    score = (late + PRIOR_LATE) / (returned + PRIOR_WEIGHT)
    if has_overdue:
        score += 0.25
    return min(score, 1.0)


def risk_level(score):
    return "high" if score >= 0.5 else "medium" if score >= 0.3 else "low"


def overdue_risk(limit=8, now=None):
    """Active borrowings that are not late yet but whose borrower often returns late."""
    now = now or timezone.now()
    active = list(
        Borrowing.objects.select_related("user", "item")
        .filter(status=BorrowingStatus.ACTIVE).order_by("due_at")
    )
    if not active:
        return []
    user_ids = {b.user_id for b in active}
    records = user_return_records(user_ids)
    overdue_users = {b.user_id for b in active if b.due_at < now}
    flagged = []
    for b in active:
        if b.due_at < now:
            continue  # already overdue: listed under Overdue Returns
        returned, late = records.get(b.user_id, (0, 0))
        has_overdue = b.user_id in overdue_users
        score = risk_score(returned, late, has_overdue)
        level = risk_level(score)
        if level == "low":
            continue
        reasons = []
        if returned:
            reasons.append(f"Returned {late} of {returned} item{'s' if returned != 1 else ''} late")
        if has_overdue:
            reasons.append("has another item overdue now")
        flagged.append({
            "borrowing": b, "score": round(score * 100), "level": level,
            "reason": "; ".join(reasons) or "No return history",
        })
    flagged.sort(key=lambda f: (-f["score"], f["borrowing"].due_at))
    return flagged[:limit]


# ---------------------------------------------------------------------------
# User-facing hints
# ---------------------------------------------------------------------------
def next_available_map(items):
    """For unavailable items: the earliest time a unit is expected back, or None.

    Uses the soonest due date of an active borrowing and the soonest return date of an
    approved reservation. Items that are out only because of repairs get None.
    """
    ids = [i.pk for i in items if i.available_qty <= 0]
    if not ids:
        return {}
    due = dict(
        Borrowing.objects.filter(item_id__in=ids, status=BorrowingStatus.ACTIVE)
        .values("item_id").annotate(first=Min("due_at")).values_list("item_id", "first")
    )
    reserved = dict(
        BorrowRequest.objects.filter(item_id__in=ids, status=RequestStatus.APPROVED)
        .values("item_id").annotate(first=Min("return_at")).values_list("item_id", "first")
    )
    result = {}
    for item_id in ids:
        options = [d for d in (due.get(item_id), reserved.get(item_id)) if d]
        result[item_id] = min(options) if options else None
    return result


def user_record(user):
    returned, late = user_return_records([user.pk]).get(user.pk, (0, 0))
    return {
        "returned": returned,
        "on_time": returned - late,
        "on_time_rate": round((returned - late) / returned * 100) if returned else None,
    }
