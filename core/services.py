"""
All inventory and transaction rules live here, so the user pages and the admin
site go through exactly the same code. Views stay thin; the database stays consistent.
"""
import logging
from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone

from .models import (
    AuditLog, Borrowing, BorrowingStatus, BorrowRequest, Item, ItemSubscription,
    NotificationType, Reason, RequestStatus, next_public_id,
)
from .notifications import create_notification

log = logging.getLogger(__name__)

REQUIRED = "Please fill out this field."


class ServiceError(Exception):
    def __init__(self, message, status=400, errors=None, code=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.errors = errors or {}
        self.code = code


def audit(actor, action, target, details=None):
    public_id = (
        getattr(target, "request_id", None)
        or getattr(target, "borrowing_id", None)
        or getattr(target, "email", None)
        or str(target.pk)
    )
    AuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        action=action,
        target_type=target.__class__.__name__,
        target_id=str(public_id),
        details=details or {},
    )


# ---------------------------------------------------------------------------
# Availability notifications
# ---------------------------------------------------------------------------
def item_stock_changed(item, was_available_qty):
    """Call after any change to an item's quantities."""
    if was_available_qty == 0 and item.available_qty > 0:
        notify_subscribers(item)


def notify_subscribers(item):
    """Notify each subscriber exactly once. The UPDATE 'claims' the subscription atomically."""
    now = timezone.now()
    for sub in ItemSubscription.objects.filter(item=item, notified_at__isnull=True).select_related("user"):
        claimed = ItemSubscription.objects.filter(pk=sub.pk, notified_at__isnull=True).update(notified_at=now)
        if claimed:
            create_notification(
                sub.user, NotificationType.ITEM_AVAILABLE, f"{item.name} is now available!", item=item,
            )


# ---------------------------------------------------------------------------
# Request form validation
# ---------------------------------------------------------------------------
def _parse_moment(data, prefix, errors):
    day, clock = data.get(f"{prefix}_date"), data.get(f"{prefix}_time")
    if not day:
        errors[f"{prefix}_date"] = REQUIRED
    if not clock:
        errors[f"{prefix}_time"] = REQUIRED
    if not day or not clock:
        return None
    try:
        naive = datetime.combine(date.fromisoformat(str(day)), datetime.strptime(str(clock)[:5], "%H:%M").time())
        return timezone.make_aware(naive, timezone.get_current_timezone())
    except ValueError:
        errors[f"{prefix}_date"] = "Enter a valid date and time."
        return None


def validate_request_payload(data):
    errors = {}
    try:
        quantity = int(data.get("quantity"))
        if quantity < 1:
            raise ValueError
    except (TypeError, ValueError):
        quantity = None
        errors["quantity"] = "Enter a quantity of 1 or more."

    borrow_at = _parse_moment(data, "borrow", errors)
    return_at = _parse_moment(data, "return", errors)
    if borrow_at and borrow_at < timezone.now() - timedelta(minutes=5):
        errors["borrow_date"] = "The borrow date and time cannot be in the past."
    if borrow_at and return_at and return_at <= borrow_at:
        errors["return_date"] = "The return date and time must be after the borrow date and time."

    reason = data.get("reason")
    reason_other = (data.get("reason_other") or "").strip()[:200]
    if reason not in {value for value, _ in Reason.choices}:
        errors["reason"] = "Select a reason for borrowing."
    elif reason == Reason.OTHER and len(reason_other) < 3:
        errors["reason_other"] = "Tell us the specific reason."

    if errors:
        raise ServiceError("Please fix the highlighted fields.", errors=errors)
    return {
        "quantity": quantity,
        "borrow_at": borrow_at,
        "return_at": return_at,
        "reason": reason,
        "reason_other": reason_other if reason == Reason.OTHER else "",
        "details": (data.get("details") or "").strip()[:500],
    }


def _lock_item(item_id):
    try:
        return Item.objects.select_for_update().get(pk=int(item_id), is_active=True)
    except (TypeError, ValueError, Item.DoesNotExist):
        raise ServiceError("This item could not be found.", status=404)


def _require_quantity(item, quantity):
    if item.available_qty <= 0:
        raise ServiceError("This item is not available right now.", status=409,
                           errors={"quantity": "No units are available."})
    if quantity > item.available_qty:
        raise ServiceError(
            f"Only {item.available_qty} unit(s) are available.", status=409,
            errors={"quantity": f"Only {item.available_qty} available."},
        )


# ---------------------------------------------------------------------------
# User actions
# ---------------------------------------------------------------------------
def create_request(user, data):
    payload = validate_request_payload(data)
    with transaction.atomic():
        item = _lock_item(data.get("item_id"))      # lock the row, then re-check availability
        _require_quantity(item, payload["quantity"])
        if BorrowRequest.objects.filter(
            user=user, item=item, status=RequestStatus.PENDING, quantity=payload["quantity"],
            borrow_at=payload["borrow_at"], return_at=payload["return_at"],
        ).exists():
            raise ServiceError("You already submitted this exact request.", status=409)
        req = BorrowRequest.objects.create(request_id=next_public_id("R"), user=user, item=item, **payload)
        audit(user, "request_created", req, {"quantity": req.quantity})
    return req


def edit_request(user, request_id, data):
    payload = validate_request_payload(data)
    with transaction.atomic():
        req = BorrowRequest.objects.select_for_update().filter(request_id=request_id, user=user).first()
        if not req:
            raise ServiceError("Request not found.", status=404)
        if req.status != RequestStatus.PENDING:
            raise ServiceError(
                "Only pending requests can be edited. Cancel the reservation first, then submit a new request.",
                status=409,
            )
        item = _lock_item(req.item_id)
        _require_quantity(item, payload["quantity"])
        for field, value in payload.items():
            setattr(req, field, value)
        req.save()
        audit(user, "request_edited", req, {"quantity": req.quantity})
    return req


def cancel_request(user, request_id):
    with transaction.atomic():
        req = BorrowRequest.objects.select_for_update().filter(request_id=request_id, user=user).first()
        if not req:
            raise ServiceError("Request not found.", status=404)
        if req.status == RequestStatus.PENDING:
            req.status = RequestStatus.CANCELED
            req.canceled_at = timezone.now()
            req.save()
            audit(user, "request_canceled", req)
        elif req.status == RequestStatus.APPROVED:
            _release_reservation(req, actor=user)
        else:
            raise ServiceError("This request can no longer be canceled.", status=409)
    return req


def _release_reservation(req, actor):
    item = Item.objects.select_for_update().get(pk=req.item_id)
    was_available = item.available_qty
    item.reserved_qty = max(0, item.reserved_qty - req.quantity)
    item.save(update_fields=["reserved_qty"])
    req.status = RequestStatus.CANCELED
    req.canceled_at = timezone.now()
    req.save()
    audit(actor, "reservation_canceled", req, {"released": req.quantity})
    create_notification(
        req.user, NotificationType.RESERVATION_CANCELED,
        f"Your reservation for {item.name} has been canceled.", request=req,
    )
    item_stock_changed(item, was_available)


# ---------------------------------------------------------------------------
# Admin actions (used by the Django admin site; the separate admin app can call them too)
# ---------------------------------------------------------------------------
def approve_request(request_pk, actor):
    with transaction.atomic():
        req = BorrowRequest.objects.select_for_update().get(pk=request_pk)
        if req.status != RequestStatus.PENDING:
            raise ServiceError(f"{req.request_id} is not pending.", status=409)
        item = Item.objects.select_for_update().get(pk=req.item_id)
        if item.available_qty < req.quantity:
            raise ServiceError(f"Not enough available units of {item.name} for {req.request_id}.", status=409)
        item.reserved_qty += req.quantity
        item.save(update_fields=["reserved_qty"])
        req.status = RequestStatus.APPROVED
        req.approval_id = next_public_id("A")
        req.approved_at = timezone.now()
        req.save()
        audit(actor, "request_approved", req, {"approval_id": req.approval_id})
        create_notification(
            req.user, NotificationType.REQUEST_APPROVED,
            f"Your request for {item.name} has been approved. The item is reserved for you.", request=req,
        )
    return req


def reject_request(request_pk, actor, reason):
    reason = (reason or "").strip()
    if not reason:
        raise ServiceError("A rejection reason is required.")
    with transaction.atomic():
        req = BorrowRequest.objects.select_for_update().get(pk=request_pk)
        if req.status != RequestStatus.PENDING:
            raise ServiceError(f"{req.request_id} is not pending.", status=409)
        req.status = RequestStatus.REJECTED
        req.rejection_reason = reason[:300]
        req.save()
        audit(actor, "request_rejected", req, {"reason": reason[:300]})
        create_notification(
            req.user, NotificationType.REQUEST_REJECTED,
            f"Your request for {req.item.name} has been declined. Reason: {reason}.", request=req,
        )
    return req


def cancel_reservation_as_admin(request_pk, actor):
    with transaction.atomic():
        req = BorrowRequest.objects.select_for_update().get(pk=request_pk)
        if req.status != RequestStatus.APPROVED:
            raise ServiceError(f"{req.request_id} has no active reservation.", status=409)
        _release_reservation(req, actor=actor)
    return req


def confirm_pickup(request_pk, actor):
    """The borrowing (and its B-ID) exists only after the item is physically handed over."""
    with transaction.atomic():
        req = BorrowRequest.objects.select_for_update().get(pk=request_pk)
        if req.status != RequestStatus.APPROVED:
            raise ServiceError(f"{req.request_id} is not an approved reservation.", status=409)
        item = Item.objects.select_for_update().get(pk=req.item_id)
        item.reserved_qty = max(0, item.reserved_qty - req.quantity)
        item.borrowed_qty += req.quantity
        item.times_borrowed += 1
        item.save(update_fields=["reserved_qty", "borrowed_qty", "times_borrowed"])
        req.status = RequestStatus.FULFILLED
        req.save()
        borrowing = Borrowing.objects.create(
            borrowing_id=next_public_id("B"), request=req, user=req.user, item=item,
            quantity=req.quantity, picked_up_at=timezone.now(), due_at=req.return_at,
        )
        audit(actor, "pickup_confirmed", borrowing, {"request": req.request_id})
    return borrowing


def confirm_return(borrowing_pk, actor, condition=None, repair_qty=0):
    """Close the borrowing, restore stock, update condition, and tell the user."""
    with transaction.atomic():
        borrowing = Borrowing.objects.select_for_update().get(pk=borrowing_pk)
        if borrowing.status != BorrowingStatus.ACTIVE:
            raise ServiceError(f"{borrowing.borrowing_id} was already returned.", status=409)
        item = Item.objects.select_for_update().get(pk=borrowing.item_id)
        was_available = item.available_qty
        item.borrowed_qty = max(0, item.borrowed_qty - borrowing.quantity)
        repair_qty = max(0, min(int(repair_qty or 0), borrowing.quantity))
        item.repair_qty += repair_qty
        if condition:
            item.condition = condition
        item.save(update_fields=["borrowed_qty", "repair_qty", "condition"])
        borrowing.status = BorrowingStatus.RETURNED
        borrowing.returned_at = timezone.now()
        borrowing.return_id = next_public_id("RT")
        borrowing.save()
        audit(actor, "return_confirmed", borrowing, {"return_id": borrowing.return_id, "to_repair": repair_qty})
        create_notification(
            borrowing.user, NotificationType.RETURN_CONFIRMED,
            f"Your return of {item.name} has been confirmed.", borrowing=borrowing,
        )
        item_stock_changed(item, was_available)
    return borrowing
