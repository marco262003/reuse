"""Custom staff-facing REUSE administration pages."""
from datetime import timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Case, Count, Q, When
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import face_login, names, registration, validators as v
from .models import (
    AuditLog, Borrowing, BorrowingStatus, BorrowRequest, Category, Condition,
    Delivery, Item, Notification, RequestStatus, User, UserType,
)
from .services import (
    ServiceError, approve_request, audit, cancel_reservation_as_admin,
    confirm_pickup, confirm_return, reject_request,
)
from .security import invalidate_user_sessions


def _ctx(request, title, **extra):
    return {"admin_title": title, "admin_user": request.user, **extra}


@staff_member_required(login_url="login")
def dashboard(request):
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly = list(
        Borrowing.objects.filter(picked_up_at__gte=month_start)
        .values("picked_up_at__date").annotate(total=Count("id")).order_by("picked_up_at__date")
    )
    return render(request, "admin/dashboard.html", _ctx(
        request, "Dashboard",
        total_items=Item.objects.filter(is_active=True).count(),
        pending_requests=BorrowRequest.objects.filter(status=RequestStatus.PENDING).count(),
        active_borrowings=Borrowing.objects.filter(status=BorrowingStatus.ACTIVE).count(),
        overdue_returns=Borrowing.objects.filter(status=BorrowingStatus.ACTIVE, due_at__lt=now).count(),
        available_units=sum(i.available_qty for i in Item.objects.filter(is_active=True)),
        borrowed_units=sum(i.borrowed_qty for i in Item.objects.filter(is_active=True)),
        repair_units=sum(i.repair_qty for i in Item.objects.filter(is_active=True)),
        request_summary=[{"label": label, "count": BorrowRequest.objects.filter(status=value).count()} for value, label in RequestStatus.choices],
        recent_requests=BorrowRequest.objects.select_related("user", "item")[:5],
        overdue=Borrowing.objects.select_related("user", "item").filter(status=BorrowingStatus.ACTIVE, due_at__lt=now).order_by("due_at")[:5],
        monthly=monthly,
        unread_notifications=Notification.objects.filter(is_read=False).count(),
    ))


@staff_member_required(login_url="login")
def items(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "all")
    qs = Item.objects.all().order_by("name")
    if q:
        qs = qs.filter(name__icontains=q)
    if status == "active": qs = qs.filter(is_active=True)
    elif status == "archived": qs = qs.filter(is_active=False)
    elif status == "repair": qs = qs.filter(repair_qty__gt=0)
    return render(request, "admin/items.html", _ctx(request, "Manage Items", items=qs, q=q, status=status, categories=Category.choices, conditions=Condition.choices, edit_item=Item.objects.filter(pk=request.GET.get("edit", "")).first() if request.GET.get("edit", "").isdigit() else None))


@staff_member_required(login_url="login")
def item_save(request):
    if request.method != "POST": return redirect("staff_items")
    item_id = request.POST.get("item_id")
    item = get_object_or_404(Item, pk=item_id) if item_id else Item()
    try:
        name = request.POST.get("name", "").strip()
        category = request.POST.get("category", "")
        condition = request.POST.get("condition", Condition.GOOD)
        total = int(request.POST.get("total_qty", "1"))
        repair = int(request.POST.get("repair_qty", str(item.repair_qty or 0)))
        description = request.POST.get("description", "").strip()
        image_url = request.POST.get("image_url", "").strip()
        uploaded_image = request.FILES.get("image")
        if uploaded_image:
            from PIL import Image as PILImage
            if uploaded_image.size > 5 * 1024 * 1024:
                raise ValueError("Item image must be 5 MB or smaller.")
            try:
                uploaded_image.seek(0)
                img = PILImage.open(uploaded_image)
                if img.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Upload a JPG, PNG, or WEBP image.")
                img.verify()
                uploaded_image.seek(0)
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError("The uploaded item image is not a valid image.") from exc
        if not name: raise ValueError("Enter an item name.")
        if category not in Category.values: raise ValueError("Select a valid category.")
        if condition not in Condition.values: raise ValueError("Select a valid condition.")
        if total < 1: raise ValueError("Total quantity must be at least 1.")
        if repair < 0: raise ValueError("Under-repair quantity cannot be negative.")
        if total < item.reserved_qty + item.borrowed_qty + repair:
            raise ValueError("Total quantity cannot be less than reserved, borrowed, and under-repair units.")
        was_available = item.available_qty if item.pk else 0
        item.name, item.category, item.condition = name, category, condition
        item.total_qty, item.repair_qty, item.description, item.image_url = total, repair, description, image_url
        if uploaded_image:
            item.image = uploaded_image
        item.full_clean()
        item.save()
        from .services import item_stock_changed
        item_stock_changed(item, was_available)
        audit(request.user, "item_created" if not item_id else "item_updated", item)
        messages.success(request, f"{item.name} saved successfully.")
    except (ValueError, TypeError) as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        messages.error(request, f"Could not save item: {exc}")
    return redirect("staff_items")


@staff_member_required(login_url="login")
def item_toggle(request, item_id):
    if request.method == "POST":
        item = get_object_or_404(Item, pk=item_id)
        item.is_active = not item.is_active
        item.save(update_fields=["is_active"])
        audit(request.user, "item_activated" if item.is_active else "item_archived", item)
        messages.success(request, f"{item.name} {'activated' if item.is_active else 'archived'}.")
    return redirect("staff_items")


@staff_member_required(login_url="login")
def requests_list(request):
    q, status = request.GET.get("q", "").strip(), request.GET.get("status", "all")
    qs = BorrowRequest.objects.select_related("user", "item")
    if q: qs = qs.filter(item__name__icontains=q) | BorrowRequest.objects.select_related("user", "item").filter(_name_matches(q, "user__")) | BorrowRequest.objects.select_related("user", "item").filter(request_id__icontains=q)
    if status in RequestStatus.values: qs = qs.filter(status=status)
    # Requests waiting on staff come first: pending, then reserved for pickup, then the rest.
    qs = qs.annotate(queue=Case(
        When(status=RequestStatus.PENDING, then=0), When(status=RequestStatus.APPROVED, then=1), default=2,
    )).order_by("queue", "-created_at", "-id")
    return render(request, "admin/requests.html", _ctx(request, "Borrowing Requests", requests=qs.distinct(), q=q, status=status, statuses=RequestStatus.choices))


@staff_member_required(login_url="login")
def request_action(request, request_id, action):
    if request.method != "POST": return redirect("staff_requests")
    req = get_object_or_404(BorrowRequest, pk=request_id)
    try:
        if action == "approve": approve_request(req.pk, request.user)
        elif action == "reject": reject_request(req.pk, request.user, request.POST.get("rejection_reason", ""))
        elif action == "pickup": confirm_pickup(req.pk, request.user)
        elif action == "cancel": cancel_reservation_as_admin(req.pk, request.user)
        else: raise ServiceError("Unknown request action.")
        messages.success(request, f"{req.request_id}: action completed.")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect("staff_requests")


@staff_member_required(login_url="login")
def borrowings(request):
    q, status = request.GET.get("q", "").strip(), request.GET.get("status", "all")
    qs = Borrowing.objects.select_related("user", "item", "request")
    if q: qs = qs.filter(item__name__icontains=q) | Borrowing.objects.select_related("user", "item", "request").filter(_name_matches(q, "user__")) | Borrowing.objects.select_related("user", "item", "request").filter(borrowing_id__icontains=q)
    if status == "active": qs = qs.filter(status=BorrowingStatus.ACTIVE)
    elif status == "returned":
        qs = qs.filter(status=BorrowingStatus.RETURNED)
    elif status == "overdue": qs = qs.filter(status=BorrowingStatus.ACTIVE, due_at__lt=timezone.now())
    elif status == "due_soon": qs = qs.filter(status=BorrowingStatus.ACTIVE, due_at__gte=timezone.now(), due_at__lte=timezone.now()+timedelta(hours=24))
    now = timezone.now()
    due_soon_ids = set(qs.filter(status=BorrowingStatus.ACTIVE, due_at__gte=now, due_at__lte=now + timedelta(hours=24)).values_list("pk", flat=True))
    return render(request, "admin/borrowings.html", _ctx(request, "Active Borrowings", borrowings=qs.distinct(), q=q, status=status, now=now, due_soon_ids=due_soon_ids))


@staff_member_required(login_url="login")
def return_management(request):
    q, status = request.GET.get("q", "").strip(), request.GET.get("status", "all")
    qs = Borrowing.objects.select_related("user", "item", "request")
    if q:
        qs = qs.filter(item__name__icontains=q) | Borrowing.objects.select_related("user", "item", "request").filter(_name_matches(q, "user__")) | Borrowing.objects.select_related("user", "item", "request").filter(borrowing_id__icontains=q)
    now = timezone.now()
    if status == "active": qs = qs.filter(status=BorrowingStatus.ACTIVE)
    elif status == "returned": qs = qs.filter(status=BorrowingStatus.RETURNED)
    elif status == "overdue": qs = qs.filter(status=BorrowingStatus.ACTIVE, due_at__lt=now)
    elif status == "due_soon": qs = qs.filter(status=BorrowingStatus.ACTIVE, due_at__gte=now, due_at__lte=now + timedelta(hours=24))
    due_soon_ids = set(qs.filter(status=BorrowingStatus.ACTIVE, due_at__gte=now, due_at__lte=now + timedelta(hours=24)).values_list("pk", flat=True))
    return render(request, "admin/borrowings.html", _ctx(request, "Return Management", borrowings=qs.distinct(), q=q, status=status, now=now, due_soon_ids=due_soon_ids))


@staff_member_required(login_url="login")
def return_action(request, borrowing_id):
    if request.method == "POST":
        borrowing = get_object_or_404(Borrowing, pk=borrowing_id)
        condition = request.POST.get("condition", Condition.GOOD)
        try:
            repair_qty = borrowing.quantity if condition == Condition.POOR else 0
            confirm_return(borrowing.pk, request.user, condition=condition, repair_qty=repair_qty)
            messages.success(request, f"Return {borrowing.borrowing_id} confirmed.")
        except (ServiceError, ValueError) as exc:
            messages.error(request, getattr(exc, "message", str(exc)))
    return redirect("staff_returns" if request.POST.get("return_to") == "returns" else "staff_borrowings")


def _name_matches(q, prefix=""):
    """A filter for people whose name parts contain every word that was typed."""
    match = Q()
    for word in q.split():
        match &= (
            Q(**{f"{prefix}first_name__icontains": word})
            | Q(**{f"{prefix}middle_name__icontains": word})
            | Q(**{f"{prefix}last_name__icontains": word})
        )
    return match


@staff_member_required(login_url="login")
def users(request):
    q, user_type = request.GET.get("q", "").strip(), request.GET.get("type", "all")
    qs = User.objects.filter(is_superuser=False).order_by("last_name", "first_name")
    if q: qs = qs.filter(_name_matches(q))
    if user_type in UserType.values: qs = qs.filter(user_type=user_type)
    return render(request, "admin/users.html", _ctx(request, "User Management", users=qs, q=q, user_type=user_type, user_types=UserType.choices))


def _read_user_form(request, user=None):
    """Return (values, errors) using the same rules as self-registration."""
    post = request.POST
    values = {
        "first_name": names.clean_name_part(post.get("first_name")),
        "middle_name": names.clean_middle_name(post.get("middle_name")),
        "last_name": names.clean_name_part(post.get("last_name")),
        "suffix": names.clean_suffix(post.get("suffix")),
        "email": v.normalize_email(post.get("email")),
        "mobile": v.normalize_mobile(post.get("mobile", "")),
        "user_type": post.get("user_type", ""),
    }
    others = User.objects.exclude(pk=user.pk) if user else User.objects.all()
    errors = []
    for name, label, required in (("first_name", "First name", True), ("middle_name", "Middle name", False), ("last_name", "Last name", True)):
        if err := names.check_name_part(values[name], required): errors.append(f"{label}: {err}")
    if err := names.check_suffix(values["suffix"]): errors.append(f"Suffix: {err}")
    if err := v.check_email(values["email"]): errors.append(err)
    elif others.filter(email__iexact=values["email"]).exists(): errors.append("This email is already registered.")
    if not values["mobile"]: errors.append("Enter a valid Philippine mobile number (+63 9XX XXX XXXX).")
    elif others.filter(mobile=values["mobile"]).exists(): errors.append("This mobile number is already registered.")
    if values["user_type"] not in UserType.values: errors.append("Select Student, Faculty or Staff.")
    return values, errors


@staff_member_required(login_url="login")
def user_new(request):
    if request.method == "POST":
        values, errors = _read_user_form(request)
        if not errors:
            # No password yet: the person sets one with the reset code sent to this school email,
            # and adds their school ID the first time they log in.
            user = User.objects.create_user(password=None, is_email_verified=True, **values)
            audit(request.user, "account_created", user)
            try:
                send_mail(
                    "Your REUSE account",
                    f"Hi {user.full_name},\n\nAn administrator created a REUSE account for you. "
                    f"Set your password here using this email address:\n{settings.SITE_URL}/reset-password/\n\n"
                    "Have your school ID ready the first time you log in.",
                    settings.DEFAULT_FROM_EMAIL, [user.email],
                )
                messages.success(request, f"{user.full_name} created. They were emailed how to set a password.")
            except Exception:  # noqa: BLE001
                messages.warning(request, f"{user.full_name} created, but the email failed. Ask them to use Reset Password.")
            return redirect("staff_user_detail", user.pk)
        for error in errors: messages.error(request, error)
    return render(request, "admin/user_new.html", _ctx(request, "Add User", user_types=UserType.choices, form=request.POST))


@staff_member_required(login_url="login")
def user_detail(request, user_id):
    user = get_object_or_404(User, pk=user_id, is_superuser=False)
    if request.method == "POST":
        values, errors = _read_user_form(request, user)
        for error in errors: messages.error(request, error)
        if not errors:
            for field, value in values.items(): setattr(user, field, value)
            user.save(update_fields=list(values))
            audit(request.user, "account_updated", user)
            messages.success(request, f"{user.full_name} saved.")
        return redirect("staff_user_detail", user.pk)
    return render(request, "admin/user_detail.html", _ctx(
        request, user.full_name, subject=user, user_types=UserType.choices,
        records=user.verification_records.all()[:20], face_ready=face_login.has_usable_template(user),
        scans_left=face_login.attempts_left(user), max_scans=settings.FACE_MAX_FAILED_SCANS,
    ))


@staff_member_required(login_url="login")
def user_delete(request, user_id):
    if request.method != "POST": return redirect("staff_user_detail", user_id)
    user = get_object_or_404(User, pk=user_id, is_superuser=False)
    if user.pk == request.user.pk:
        messages.error(request, "You cannot delete your own account here.")
    elif user.requests.exists() or user.borrowings.exists():
        # Borrowing records must survive, so these accounts can only be deactivated.
        messages.error(request, "This account has borrowing history. Deactivate it instead.")
    else:
        name = user.full_name
        registration.discard_id_images(user)
        if user.profile_image: user.profile_image.delete(save=False)
        audit(request.user, "account_deleted", user)
        user.delete()  # the face template and the verification log go with the account
        messages.success(request, f"{name} deleted.")
        return redirect("staff_users")
    return redirect("staff_user_detail", user_id)


@staff_member_required(login_url="login")
def user_face_reset(request, user_id):
    """Forget the person's face template. They add their school ID again the next time they log in."""
    if request.method == "POST":
        user = get_object_or_404(User, pk=user_id, is_superuser=False)
        registration.discard_id_images(user)
        user.face_template, user.face_model, user.face_enrolled_at = None, "", None
        user.save(update_fields=["face_template", "face_model", "face_enrolled_at"])
        audit(request.user, "face_reset", user)
        messages.success(request, f"{user.full_name} will be asked for their school ID at their next login.")
    return redirect("staff_user_detail", user_id)


@staff_member_required(login_url="login")
def user_toggle(request, user_id):
    if request.method == "POST":
        user = get_object_or_404(User, pk=user_id, is_superuser=False)
        if user.pk == request.user.pk:
            messages.error(request, "You cannot deactivate your own account here.")
        else:
            user.is_active = not user.is_active
            user.save(update_fields=["is_active"])
            if not user.is_active: invalidate_user_sessions(user)
            audit(request.user, "account_activated" if user.is_active else "account_deactivated", user)
            messages.success(request, f"{user.full_name} {'activated' if user.is_active else 'deactivated'}.")
    return redirect("staff_users")


@staff_member_required(login_url="login")
def notifications(request):
    qs = Notification.objects.select_related("user", "request", "borrowing").all()[:100]
    return render(request, "admin/notifications.html", _ctx(request, "Alerts", notifications=qs))


@staff_member_required(login_url="login")
def logout_staff(request):
    if request.method == "POST":
        from django.contrib.auth import logout
        logout(request)
        return redirect("login")
    return redirect("staff_dashboard")
