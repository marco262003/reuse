from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import (
    AuditLog, Borrowing, BorrowRequest, Delivery, Item, ItemSubscription, Notification, User,
)
from .security import invalidate_user_sessions
from .services import (
    ServiceError, approve_request, audit, cancel_reservation_as_admin, confirm_pickup,
    confirm_return, item_stock_changed, reject_request,
)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class UserCreateForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email", "full_name", "mobile", "user_type")


class UserEditForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserEditForm
    add_form = UserCreateForm
    ordering = ("email",)
    list_display = ("email", "full_name", "user_type", "is_email_verified", "is_active")
    list_filter = ("user_type", "is_email_verified", "is_active", "is_staff")
    search_fields = ("email", "full_name", "mobile")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "mobile", "user_type", "is_email_verified", "notify_email", "notify_sms")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "full_name", "mobile", "user_type", "password1", "password2")}),
    )
    actions = ["deactivate_accounts", "reactivate_accounts"]

    @admin.action(description="Deactivate selected accounts (blocks login, ends sessions)")
    def deactivate_accounts(self, request, queryset):
        for user in queryset:
            user.is_active = False
            user.save(update_fields=["is_active"])
            invalidate_user_sessions(user)
            audit(request.user, "account_deactivated", user)
        self.message_user(request, f"{queryset.count()} account(s) deactivated.")

    @admin.action(description="Reactivate selected accounts")
    def reactivate_accounts(self, request, queryset):
        for user in queryset:
            user.is_active = True
            user.save(update_fields=["is_active"])
            audit(request.user, "account_reactivated", user)
        self.message_user(request, f"{queryset.count()} account(s) reactivated.")


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "condition", "total_qty", "reserved_qty", "borrowed_qty",
                    "repair_qty", "available", "is_active")
    list_filter = ("category", "condition", "is_active")
    search_fields = ("name",)
    readonly_fields = ("times_borrowed",)
    actions = ["mark_repaired"]

    @admin.display(description="Available")
    def available(self, obj):
        return obj.available_qty

    def save_model(self, request, obj, form, change):
        was_available = Item.objects.get(pk=obj.pk).available_qty if change else 0
        super().save_model(request, obj, form, change)
        item_stock_changed(obj, was_available)  # notifies subscribers if stock came back

    @admin.action(description="Mark all under-repair units as repaired")
    def mark_repaired(self, request, queryset):
        for item in queryset:
            was_available = item.available_qty
            item.repair_qty = 0
            item.save(update_fields=["repair_qty"])
            audit(request.user, "item_repaired", item)
            item_stock_changed(item, was_available)
        self.message_user(request, "Items marked as repaired.")


# ---------------------------------------------------------------------------
# Transactions (status is read-only: it can only change through the actions below)
# ---------------------------------------------------------------------------
def _run(modeladmin, request, queryset, fn, success, *extra):
    done = 0
    for obj in queryset:
        try:
            fn(obj.pk, request.user, *extra)
            done += 1
        except ServiceError as exc:
            modeladmin.message_user(request, exc.message, level=messages.ERROR)
    if done:
        modeladmin.message_user(request, f"{done} {success}.")


@admin.register(BorrowRequest)
class BorrowRequestAdmin(admin.ModelAdmin):
    list_display = ("request_id", "user", "item", "quantity", "borrow_at", "return_at", "status", "approval_id")
    list_filter = ("status", "item__category")
    search_fields = ("request_id", "approval_id", "user__email", "user__full_name", "item__name")
    readonly_fields = ("request_id", "approval_id", "user", "item", "quantity", "borrow_at", "return_at",
                       "reason", "reason_other", "details", "status", "approved_at", "canceled_at")
    actions = ["approve", "reject", "cancel_reservation", "confirm_pickup_action"]

    @admin.action(description="Approve selected pending requests (reserves stock)")
    def approve(self, request, queryset):
        _run(self, request, queryset, approve_request, "request(s) approved")

    @admin.action(description="Reject selected requests (uses the saved 'Rejection reason')")
    def reject(self, request, queryset):
        for req in queryset:
            try:
                reject_request(req.pk, request.user, req.rejection_reason)
            except ServiceError as exc:
                self.message_user(request, f"{req.request_id}: {exc.message}", level=messages.ERROR)

    @admin.action(description="Cancel selected reservations (releases stock)")
    def cancel_reservation(self, request, queryset):
        _run(self, request, queryset, cancel_reservation_as_admin, "reservation(s) canceled")

    @admin.action(description="Confirm pickup (creates the Borrowing ID)")
    def confirm_pickup_action(self, request, queryset):
        _run(self, request, queryset, confirm_pickup, "pickup(s) confirmed")


@admin.register(Borrowing)
class BorrowingAdmin(admin.ModelAdmin):
    list_display = ("borrowing_id", "user", "item", "quantity", "picked_up_at", "due_at", "returned_at", "status")
    list_filter = ("status",)
    search_fields = ("borrowing_id", "user__email", "item__name")
    readonly_fields = [f.name for f in Borrowing._meta.fields]
    actions = ["confirm_return_action", "confirm_return_to_repair"]

    @admin.action(description="Confirm return (item is in good shape)")
    def confirm_return_action(self, request, queryset):
        _run(self, request, queryset, confirm_return, "return(s) confirmed")

    @admin.action(description="Confirm return and send all returned units to repair")
    def confirm_return_to_repair(self, request, queryset):
        for borrowing in queryset:
            try:
                confirm_return(borrowing.pk, request.user, condition="poor", repair_qty=borrowing.quantity)
            except ServiceError as exc:
                self.message_user(request, exc.message, level=messages.ERROR)


# ---------------------------------------------------------------------------
# Read-only audit trail and delivery tracking
# ---------------------------------------------------------------------------
class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "actor", "action", "target_type", "target_id")
    list_filter = ("action", "target_type")
    search_fields = ("target_id", "actor__email")


class DeliveryInline(admin.TabularInline):
    model = Delivery
    extra = 0
    can_delete = False
    readonly_fields = ("channel", "status", "attempts", "error", "attempted_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "user", "type", "message", "is_read")
    list_filter = ("type", "is_read")
    search_fields = ("user__email", "message")
    inlines = [DeliveryInline]


admin.site.register(ItemSubscription)
