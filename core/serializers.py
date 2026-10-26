"""Turn model objects into the dictionaries the frontend expects."""
from django.utils import timezone

from .models import BorrowingStatus, NotificationType, RequestStatus


def iso(value):
    return timezone.localtime(value).isoformat() if value else None


def item_brief(item):
    return {
        "id": item.pk,
        "name": item.name,
        "image_url": (item.image.url if item.image else item.image_url),
        "category": item.category,
        "category_label": item.get_category_display(),
    }


def item_dict(item, subscribed=False, pending_request_id=None):
    data = item_brief(item)
    data.update(
        description=item.description,
        condition=item.condition,
        condition_label=item.get_condition_display(),
        pickup_return_area=item.pickup_return_area,
        available_qty=item.available_qty,
        total_qty=item.total_qty,
        times_borrowed=item.times_borrowed,
        date_added=iso(item.date_added),
        is_available=item.available_qty > 0,
        unavailable_reason=item.unavailable_reason,
        subscribed=subscribed,
        pending_request_id=pending_request_id,
    )
    return data


def receipts_for(req):
    """The receipt IDs that exist so far for one transaction."""
    receipts = [{"type": "Request", "id": req.request_id}]
    if req.approval_id:
        receipts.append({"type": "Approval", "id": req.approval_id})
    borrowing = getattr(req, "borrowing", None) if req.status == RequestStatus.FULFILLED else None
    if borrowing:
        receipts.append({"type": "Borrowing", "id": borrowing.borrowing_id})
    return receipts


def request_dict(req):
    borrowing = getattr(req, "borrowing", None) if req.status == RequestStatus.FULFILLED else None
    return {
        "id": req.request_id,
        "approval_id": req.approval_id,
        "borrowing_id": borrowing.borrowing_id if borrowing else None,
        "item": item_brief(req.item),
        "quantity": req.quantity,
        "borrow_at": iso(req.borrow_at),
        "return_at": iso(req.return_at),
        "reason": req.reason,
        "reason_label": req.get_reason_display(),
        "reason_other": req.reason_other,
        "details": req.details,
        "purpose": req.purpose,
        "status": req.status,
        "status_label": req.get_status_display(),
        "rejection_reason": req.rejection_reason,
        "created_at": iso(req.created_at),
        "can_edit": req.status == RequestStatus.PENDING,
        "can_cancel": req.status in (RequestStatus.PENDING, RequestStatus.APPROVED),
        "receipts": receipts_for(req),
    }


def borrowing_status(borrowing):
    """Outstanding items are Overdue or Due Soon; this is recalculated on every request."""
    if borrowing.status == BorrowingStatus.RETURNED:
        return "returned", "Returned"
    if borrowing.due_at < timezone.now():
        return "overdue", "Overdue"
    return "due_soon", "Due Soon"


def borrowing_dict(borrowing):
    status, label = borrowing_status(borrowing)
    req = borrowing.request
    receipts = [{"type": "Request", "id": req.request_id}]
    if req.approval_id:
        receipts.append({"type": "Approval", "id": req.approval_id})
    receipts.append({"type": "Borrowing", "id": borrowing.borrowing_id})
    return {
        "kind": "borrowing",
        "id": borrowing.borrowing_id,
        "request_id": req.request_id,
        "approval_id": req.approval_id,
        "return_id": borrowing.return_id,
        "item": item_brief(borrowing.item),
        "quantity": borrowing.quantity,
        "picked_up_at": iso(borrowing.picked_up_at),
        "due_at": iso(borrowing.due_at),
        "returned_at": iso(borrowing.returned_at),
        "status": status,
        "status_label": label,
        "purpose": req.purpose,
        "pickup_area": borrowing.item.pickup_return_area,
        "return_area": borrowing.item.pickup_return_area,
        "receipts": receipts,
    }


def canceled_reservation_dict(req):
    """A reservation that was approved and then canceled before pickup (shown in history)."""
    return {
        "kind": "reservation",
        "id": req.request_id,
        "request_id": req.request_id,
        "approval_id": req.approval_id,
        "item": item_brief(req.item),
        "quantity": req.quantity,
        "picked_up_at": None,
        "due_at": iso(req.return_at),
        "returned_at": None,
        "canceled_at": iso(req.canceled_at),
        "status": "canceled",
        "status_label": "Canceled",
        "purpose": req.purpose,
        "pickup_area": req.item.pickup_return_area,
        "return_area": req.item.pickup_return_area,
        "receipts": receipts_for(req),
    }


def notification_dict(note):
    link = None
    if note.type in (NotificationType.REQUEST_APPROVED, NotificationType.REQUEST_REJECTED,
                     NotificationType.RESERVATION_CANCELED) and note.request_id:
        link = f"/requests/?open={note.request.request_id}"
    elif note.type in (NotificationType.DUE_SOON, NotificationType.OVERDUE) and note.borrowing_id:
        link = f"/returns/?open={note.borrowing.borrowing_id}"
    elif note.type == NotificationType.RETURN_CONFIRMED and note.borrowing_id:
        link = f"/borrowed/?open={note.borrowing.borrowing_id}"
    elif note.type == NotificationType.ITEM_AVAILABLE and note.item_id:
        link = f"/browse/?item={note.item_id}"
    return {
        "id": note.pk,
        "type": note.type,
        "message": note.message,
        "is_read": note.is_read,
        "created_at": iso(note.created_at),
        "link": link,
    }


def user_dict(user):
    return {
        "id": user.pk,
        "full_name": user.full_name,
        "short_name": user.short_name,
        "email": user.email,
        "mobile": user.mobile or "",
        "user_type": user.user_type,
        "user_type_label": user.get_user_type_display(),
        "profile_image_url": user.profile_image.url if user.profile_image else "",
    }
