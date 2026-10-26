from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class UserType(models.TextChoices):
    STUDENT = "student", "Student"
    FACULTY = "faculty", "Faculty"
    STAFF = "staff", "Staff"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_email_verified", True)
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150)
    mobile = models.CharField(max_length=13, unique=True, null=True, blank=True)  # stored as +639XXXXXXXXX
    user_type = models.CharField(max_length=10, choices=UserType.choices, default=UserType.STUDENT)

    is_active = models.BooleanField(default=True)  # False = deactivated by an admin
    is_staff = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)
    profile_image = models.ImageField(upload_to="profiles/", blank=True, null=True)

    # Consent for email / SMS notifications (web notifications are always saved).
    notify_email = models.BooleanField(default=True)
    notify_sms = models.BooleanField(default=True)

    date_joined = models.DateTimeField(default=timezone.now)
    identity_verification_status = models.CharField(
        max_length=20,
        default="not_started",
    )

    identity_verified = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    def __str__(self):
        return self.email

    @property
    def short_name(self):
        parts = self.full_name.split()
        if len(parts) <= 2:
            return self.full_name
        return f"{parts[0]} {parts[-1]}"


# ---------------------------------------------------------------------------
# Permanent, never-reused IDs (R00001, A00001, B00001, RT00001)
# ---------------------------------------------------------------------------
class Counter(models.Model):
    name = models.CharField(max_length=10, primary_key=True)
    value = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.name}: {self.value}"


def next_public_id(prefix):
    """Atomically hand out the next ID for a prefix. IDs are generated only here."""
    with transaction.atomic():
        Counter.objects.get_or_create(name=prefix)
        counter = Counter.objects.select_for_update().get(name=prefix)
        counter.value += 1
        counter.save(update_fields=["value"])
        return f"{prefix}{counter.value:05d}"


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
class Category(models.TextChoices):
    BOOKS = "books", "Books and Learning Materials"
    SUPPLIES = "supplies", "School and Office Supplies"
    SPORTS = "sports", "Sports Equipment"
    AV = "av", "Audio-Visual and Electronic Equipment"
    TOOLS = "tools", "Tools and Event Materials"


class Condition(models.TextChoices):
    NEW = "new", "New"
    GOOD = "good", "Good"
    FAIR = "fair", "Fair"
    POOR = "poor", "Poor"


class ItemQuerySet(models.QuerySet):
    def with_available(self):
        return self.annotate(avail=F("total_qty") - F("reserved_qty") - F("borrowed_qty") - F("repair_qty"))

    def available(self):
        return self.filter(is_active=True).with_available().filter(avail__gt=0)


class Item(models.Model):
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=12, choices=Category.choices)
    condition = models.CharField(max_length=8, choices=Condition.choices, default=Condition.GOOD)
    image_url = models.URLField(blank=True)  # Optional externally hosted image
    image = models.ImageField(upload_to="items/", blank=True, null=True)
    pickup_return_area = models.CharField(max_length=150, default="CICS Designated Pickup/Return Area")

    # One inventory, shared by the user side and the admin side.
    total_qty = models.PositiveIntegerField(default=1)
    reserved_qty = models.PositiveIntegerField(default=0)    # approved, not yet picked up
    borrowed_qty = models.PositiveIntegerField(default=0)    # picked up, not yet returned
    repair_qty = models.PositiveIntegerField(default=0)      # under repair

    times_borrowed = models.PositiveIntegerField(default=0)
    date_added = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=True)

    objects = ItemQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(total_qty__gte=F("reserved_qty") + F("borrowed_qty") + F("repair_qty")),
                name="item_units_never_exceed_total",
            )
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.reserved_qty + self.borrowed_qty + self.repair_qty > self.total_qty:
            raise ValidationError("Reserved + borrowed + under-repair units cannot exceed the total quantity.")

    @property
    def available_qty(self):
        return max(0, self.total_qty - self.reserved_qty - self.borrowed_qty - self.repair_qty)

    @property
    def unavailable_reason(self):
        if self.available_qty > 0:
            return ""
        if self.borrowed_qty:
            return "Currently Borrowed"
        if self.reserved_qty:
            return "Fully Reserved"
        if self.repair_qty:
            return "Under Repair"
        return "Out of Stock"


class ItemSubscription(models.Model):
    """'Notify When Available'. notified_at stops duplicate notifications."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="subscriptions")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="subscriptions")
    created_at = models.DateTimeField(auto_now_add=True)
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "item"], name="one_subscription_per_user_item")]


# ---------------------------------------------------------------------------
# Requests, approvals, borrowings
# ---------------------------------------------------------------------------
class RequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved / Reserved"
    REJECTED = "rejected", "Rejected"
    CANCELED = "canceled", "Canceled"
    FULFILLED = "fulfilled", "Picked up"


class Reason(models.TextChoices):
    CLASS = "class_activity", "Class Activity"
    PROJECT = "school_project", "School Project"
    ORG = "org_event", "Organization Event"
    FACULTY = "faculty_staff", "Faculty/Staff Activity"
    OTHER = "other", "Other"


class BorrowRequest(models.Model):
    request_id = models.CharField(max_length=10, unique=True, editable=False)    # R00001
    approval_id = models.CharField(max_length=10, unique=True, null=True, blank=True, editable=False)  # A00001
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="requests")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="requests")
    quantity = models.PositiveIntegerField()
    borrow_at = models.DateTimeField()
    return_at = models.DateTimeField()
    reason = models.CharField(max_length=20, choices=Reason.choices)
    reason_other = models.CharField(max_length=200, blank=True)
    details = models.TextField(blank=True)

    status = models.CharField(max_length=10, choices=RequestStatus.choices, default=RequestStatus.PENDING)
    rejection_reason = models.CharField(max_length=300, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"{self.request_id} - {self.item.name} x{self.quantity}"

    @property
    def purpose(self):
        if self.reason == Reason.OTHER:
            text = self.reason_other
        else:
            text = self.get_reason_display()
        return f"{text}: {self.details}" if self.details else text


class BorrowingStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    RETURNED = "returned", "Returned"


class Borrowing(models.Model):
    """Created only when the admin confirms the physical pickup."""

    borrowing_id = models.CharField(max_length=10, unique=True, editable=False)  # B00001
    return_id = models.CharField(max_length=10, unique=True, null=True, blank=True, editable=False)  # RT00001
    request = models.OneToOneField(BorrowRequest, on_delete=models.PROTECT, related_name="borrowing")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="borrowings")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="borrowings")
    quantity = models.PositiveIntegerField()
    picked_up_at = models.DateTimeField()
    due_at = models.DateTimeField()
    returned_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=BorrowingStatus.choices, default=BorrowingStatus.ACTIVE)

    class Meta:
        ordering = ["-picked_up_at", "-id"]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"{self.borrowing_id} - {self.item.name} x{self.quantity}"

    @property
    def is_overdue(self):
        return self.status == BorrowingStatus.ACTIVE and self.due_at < timezone.now()


# ---------------------------------------------------------------------------
# Notifications (saved in the database, linked to the user and the transaction)
# ---------------------------------------------------------------------------
class NotificationType(models.TextChoices):
    ACCOUNT_VERIFIED = "account_verified", "Account verified"
    REQUEST_APPROVED = "request_approved", "Request approved"
    REQUEST_REJECTED = "request_rejected", "Request rejected"
    RESERVATION_CANCELED = "reservation_canceled", "Reservation canceled"
    ITEM_AVAILABLE = "item_available", "Item available"
    DUE_SOON = "due_soon", "Due soon"
    OVERDUE = "overdue", "Overdue"
    RETURN_CONFIRMED = "return_confirmed", "Return confirmed"


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    type = models.CharField(max_length=24, choices=NotificationType.choices)
    message = models.CharField(max_length=400)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    request = models.ForeignKey(BorrowRequest, null=True, blank=True, on_delete=models.SET_NULL)
    borrowing = models.ForeignKey(Borrowing, null=True, blank=True, on_delete=models.SET_NULL)
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.SET_NULL)

    # e.g. "overdue:12" - makes sure the same reminder is never created twice.
    dedupe_key = models.CharField(max_length=80, null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "is_read"])]
        constraints = [models.UniqueConstraint(fields=["user", "dedupe_key"], name="unique_notification_per_dedupe_key")]


class DeliveryChannel(models.TextChoices):
    WEB = "web", "REUSE web"
    EMAIL = "email", "Email"
    SMS = "sms", "SMS"


class DeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class Delivery(models.Model):
    """One row per notification per channel: tracks what was REALLY delivered."""

    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="deliveries")
    channel = models.CharField(max_length=5, choices=DeliveryChannel.choices)
    status = models.CharField(max_length=8, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    attempted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["notification", "channel"], name="one_delivery_per_channel")]


# ---------------------------------------------------------------------------
# Password reset + audit
# ---------------------------------------------------------------------------
class PasswordResetCode(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reset_codes")
    channel = models.CharField(max_length=5, choices=[("email", "Email"), ("sms", "SMS")])
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]


class AuditLog(models.Model):
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=40)
    target_type = models.CharField(max_length=40)
    target_id = models.CharField(max_length=40)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.target_type}#{self.target_id}"
