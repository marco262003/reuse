"""Items, requests, borrowings, notifications and dashboard endpoints.

Every query is filtered by request.user, so changing an ID in the URL can never
show another person's data (it simply answers 404).
"""
import re

from django.db.models import Case, Value, When

from .analytics import next_available_map, user_record
from .api import api_view, fail, ok
from .models import (
    Borrowing, BorrowingStatus, BorrowRequest, Category, Item, ItemSubscription,
    Notification, RequestStatus,
)
from .serializers import (
    borrowing_dict, borrowing_status, canceled_reservation_dict, iso, item_dict,
    notification_dict, request_dict,
)
from .services import ServiceError, cancel_request, create_request, edit_request


def service_call(fn, *args):
    """Run a service function and turn ServiceError into a JSON error."""
    try:
        return fn(*args), None
    except ServiceError as exc:
        return None, fail(exc.message, exc.status, errors=exc.errors, code=exc.code)


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------
def _subscription_and_pending_maps(user, item_ids):
    subscribed = set(
        ItemSubscription.objects.filter(user=user, item_id__in=item_ids, notified_at__isnull=True)
        .values_list("item_id", flat=True)
    )
    pending = dict(
        BorrowRequest.objects.filter(user=user, item_id__in=item_ids, status=RequestStatus.PENDING)
        .order_by("created_at").values_list("item_id", "request_id")
    )  # newest wins because later rows overwrite earlier ones
    return subscribed, pending


SORTS = {
    # Available items first, then by name: what most people want when browsing.
    "available": (Case(When(avail__gt=0, then=Value(0)), default=Value(1)), "name"),
    "name": ("name",),
    "newest": ("-date_added", "name"),
    "popular": ("-times_borrowed", "name"),
}


@api_view(["GET"])
def items_list(request):
    q = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    sort = request.GET.get("sort", "available")
    items = Item.objects.filter(is_active=True).with_available()
    if q:
        items = items.filter(name__icontains=q)          # partial match
    if category in Category.values:
        items = items.filter(category=category)          # works together with the search
    if request.GET.get("available") == "1":
        items = items.filter(avail__gt=0)
    items = list(items.order_by(*SORTS.get(sort, SORTS["available"])))
    subscribed, pending = _subscription_and_pending_maps(request.user, [i.pk for i in items])
    back = next_available_map(items)
    return ok({"items": [item_dict(i, i.pk in subscribed, pending.get(i.pk), back.get(i.pk)) for i in items]})


@api_view(["GET"])
def item_detail(request, item_id):
    item = Item.objects.filter(pk=item_id, is_active=True).first()
    if not item:
        return fail("Item not found.", 404)
    subscribed, pending = _subscription_and_pending_maps(request.user, [item.pk])
    back = next_available_map([item])
    return ok({"item": item_dict(item, item.pk in subscribed, pending.get(item.pk), back.get(item.pk))})


@api_view(["POST"])
def item_subscribe(request, item_id):
    item = Item.objects.filter(pk=item_id, is_active=True).first()
    if not item:
        return fail("Item not found.", 404)
    if item.available_qty > 0:
        return fail("This item is already available. You can request it now.", 409)
    ItemSubscription.objects.update_or_create(
        user=request.user, item=item, defaults={"notified_at": None},
    )
    return ok({"message": "Notification enabled."})


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
def _own_request(user, request_id):
    return (
        BorrowRequest.objects.select_related("item", "borrowing")
        .filter(request_id=request_id, user=user).first()
    )


@api_view(["GET", "POST"])
def requests_collection(request):
    if request.method == "POST":
        req, error = service_call(create_request, request.user, request.data)
        if error:
            return error
        req = _own_request(request.user, req.request_id)
        return ok({"request": request_dict(req)}, status=201)

    q = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    qs = BorrowRequest.objects.select_related("item", "borrowing").filter(user=request.user)
    if q:
        qs = qs.filter(item__name__icontains=q)
    if category in Category.values:
        qs = qs.filter(item__category=category)
    return ok({"requests": [request_dict(r) for r in qs]})


@api_view(["GET", "PATCH"])
def request_detail(request, request_id):
    if request.method == "PATCH":
        _, error = service_call(edit_request, request.user, request_id, request.data)
        if error:
            return error
    req = _own_request(request.user, request_id)
    if not req:
        return fail("Request not found.", 404)
    return ok({"request": request_dict(req)})


@api_view(["POST"])
def request_cancel(request, request_id):
    _, error = service_call(cancel_request, request.user, request_id)
    if error:
        return error
    req = _own_request(request.user, request_id)
    return ok({"request": request_dict(req), "message": "Request canceled."})


# ---------------------------------------------------------------------------
# Borrowings and returns
# ---------------------------------------------------------------------------
@api_view(["GET"])
def borrowings_list(request):
    user = request.user
    base = Borrowing.objects.select_related("item", "request").filter(user=user)
    active = base.filter(status=BorrowingStatus.ACTIVE).order_by("due_at")
    returned = base.filter(status=BorrowingStatus.RETURNED)
    canceled = BorrowRequest.objects.select_related("item").filter(
        user=user, status=RequestStatus.CANCELED, approval_id__isnull=False,
    )
    past = [(b.returned_at, borrowing_dict(b)) for b in returned]
    past += [(r.canceled_at or r.updated_at, canceled_reservation_dict(r)) for r in canceled]
    past.sort(key=lambda pair: pair[0], reverse=True)
    return ok({"active": [borrowing_dict(b) for b in active], "past": [d for _, d in past]})


@api_view(["GET"])
def borrowing_detail(request, borrowing_id):
    b = Borrowing.objects.select_related("item", "request").filter(borrowing_id=borrowing_id, user=request.user).first()
    if not b:
        return fail("Borrowing not found.", 404)
    return ok({"borrowing": borrowing_dict(b)})


@api_view(["GET"])
def returns_list(request):
    """Only physically borrowed, not-yet-returned items. Overdue first, then soonest due."""
    qs = (
        Borrowing.objects.select_related("item", "request")
        .filter(user=request.user, status=BorrowingStatus.ACTIVE).order_by("due_at")
    )
    return ok({"returns": [borrowing_dict(b) for b in qs]})


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------
# Each stage of a transaction has its own ID; every one of them opens the same receipt.
RECEIPT_LOOKUPS = (
    (r"R\d{5}", "request_id"),
    (r"A\d{5}", "approval_id"),
    (r"B\d{5}", "borrowing__borrowing_id"),
    (r"RT\d{5}", "borrowing__return_id"),
)


@api_view(["GET"])
def receipt_detail(request, ref_id):
    """The one receipt for a transaction, numbered by its request ID."""
    field = next((name for pattern, name in RECEIPT_LOOKUPS if re.fullmatch(pattern, ref_id)), None)
    req = (
        BorrowRequest.objects.select_related("item", "user", "borrowing")
        .filter(user=request.user, **{field: ref_id}).first()
        if field else None
    )
    if not req:
        return fail("Receipt not found.", 404)

    borrowing = getattr(req, "borrowing", None)
    status = borrowing_status(borrowing)[1] if borrowing else req.get_status_display()
    return ok({"receipt": {
        "id": req.request_id,
        "request_id": req.request_id,
        "approval_id": req.approval_id,
        "borrowing_id": borrowing.borrowing_id if borrowing else None,
        "return_id": borrowing.return_id if borrowing else None,
        "requester": {
            "name": req.user.full_name, "email": req.user.email,
            "user_type": req.user.get_user_type_display(),
        },
        "item": req.item.name,
        "quantity": req.quantity,
        "requested_at": iso(req.created_at),
        "approved_at": iso(req.approved_at),
        "borrow_at": iso(req.borrow_at),
        "return_at": iso(req.return_at),
        "picked_up_at": iso(borrowing.picked_up_at) if borrowing else None,
        "returned_at": iso(borrowing.returned_at) if borrowing else None,
        "status": status,
        "purpose": req.purpose,
    }})


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
@api_view(["GET"])
def notifications_list(request):
    qs = Notification.objects.select_related("request", "borrowing").filter(user=request.user)[:200]
    return ok({"notifications": [notification_dict(n) for n in qs]})


@api_view(["POST"])
def notification_read(request, pk):
    updated = Notification.objects.filter(pk=pk, user=request.user).update(is_read=True)
    if not updated:
        return fail("Notification not found.", 404)
    return ok()


@api_view(["POST"])
def notifications_read_all(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return ok()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@api_view(["GET"])
def dashboard(request):
    user = request.user
    active = Borrowing.objects.select_related("item", "request").filter(user=user, status=BorrowingStatus.ACTIVE)
    recent_requests = BorrowRequest.objects.select_related("item", "borrowing").filter(user=user)[:5]
    ready_for_pickup = BorrowRequest.objects.select_related("item").filter(
        user=user, status=RequestStatus.APPROVED,
    ).order_by("borrow_at")[:5]
    recent_items = Item.objects.available().order_by("-date_added")[:5]
    open_requests = BorrowRequest.objects.filter(
        user=user, status__in=[RequestStatus.PENDING, RequestStatus.APPROVED],
    ).count()
    return ok({
        "counts": {
            "available_items": Item.objects.available().count(),
            "my_requests": open_requests,
            "active_borrowings": active.count(),
            "items_to_return": active.count(),
            "alerts": Notification.objects.filter(user=user, is_read=False).count(),
        },
        "record": user_record(user),
        "has_any_request": BorrowRequest.objects.filter(user=user).exists(),
        "recent_requests": [request_dict(r) for r in recent_requests],
        "ready_for_pickup": [request_dict(r) for r in ready_for_pickup],
        "upcoming_returns": [borrowing_dict(b) for b in active.order_by("due_at")[:5]],
        "recent_items": [item_dict(i) for i in recent_items],
    })
