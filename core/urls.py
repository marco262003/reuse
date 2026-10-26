from django.urls import path
from django.views.generic import RedirectView

from . import api_auth, api_data, views_pages as pages, views_admin as staff_pages

urlpatterns = [
    # --- Pages -------------------------------------------------------------
    path("", RedirectView.as_view(pattern_name="dashboard", permanent=False)),
    path("login/", pages.login_page, name="login"),
    path("create-account/", pages.create_account_page, name="create_account"),
    path("reset-password/", pages.reset_password_page, name="reset_password"),
    path("change-password/", pages.change_password_page, name="change_password"),
    path("verify-email/<str:token>/", pages.verify_email, name="verify_email"),
    path("dashboard/", pages.dashboard_page, name="dashboard"),
    path("browse/", pages.browse_page, name="browse"),
    path("requests/", pages.requests_page, name="requests"),
    path("borrowed/", pages.borrowed_page, name="borrowed"),
    path("returns/", pages.returns_page, name="returns"),
    path("profile/", pages.profile_page, name="profile"),
    path("notifications/", pages.notifications_page, name="notifications"),

    # --- Custom Admin Dashboard --------------------------------------------
    path("admin-dashboard/", staff_pages.dashboard, name="staff_dashboard"),
    path("admin-dashboard/items/", staff_pages.items, name="staff_items"),
    path("admin-dashboard/items/save/", staff_pages.item_save, name="staff_item_save"),
    path(
        "admin-dashboard/items/<int:item_id>/toggle/",
        staff_pages.item_toggle,
        name="staff_item_toggle",
    ),
    path("admin-dashboard/requests/", staff_pages.requests_list, name="staff_requests"),
    path(
        "admin-dashboard/requests/<int:request_id>/<str:action>/",
        staff_pages.request_action,
        name="staff_request_action",
    ),
    path("admin-dashboard/borrowings/", staff_pages.borrowings, name="staff_borrowings"),
    path("admin-dashboard/returns/", staff_pages.return_management, name="staff_returns"),
    path(
        "admin-dashboard/borrowings/<int:borrowing_id>/return/",
        staff_pages.return_action,
        name="staff_return_action",
    ),
    path("admin-dashboard/users/", staff_pages.users, name="staff_users"),
    path(
        "admin-dashboard/users/<int:user_id>/toggle/",
        staff_pages.user_toggle,
        name="staff_user_toggle",
    ),
    path(
        "admin-dashboard/notifications/",
        staff_pages.notifications,
        name="staff_notifications",
    ),
    path("admin-dashboard/logout/", staff_pages.logout_staff, name="staff_logout"),

    # --- Auth API ----------------------------------------------------------
    path("api/auth/register/", api_auth.register),
    path("api/auth/resend-verification/", api_auth.resend_verification),
    path("api/auth/login/", api_auth.login_view),
    path("api/auth/logout/", api_auth.logout_view),
    path("api/auth/reset/send-code/", api_auth.reset_send_code),
    path("api/auth/reset/verify-code/", api_auth.reset_verify_code),
    path("api/auth/reset/change-password/", api_auth.reset_change_password),
    path("api/me/", api_auth.me),
    path("api/me/profile-image/", api_auth.upload_profile_image),
    path("api/me/change-password/", api_auth.change_password),

    # --- Data API ----------------------------------------------------------
    path("api/dashboard/", api_data.dashboard),
    path("api/items/", api_data.items_list),
    path("api/items/<int:item_id>/", api_data.item_detail),
    path("api/items/<int:item_id>/subscribe/", api_data.item_subscribe),
    path("api/requests/", api_data.requests_collection),
    path("api/requests/<str:request_id>/", api_data.request_detail),
    path("api/requests/<str:request_id>/cancel/", api_data.request_cancel),
    path("api/borrowings/", api_data.borrowings_list),
    path("api/borrowings/<str:borrowing_id>/", api_data.borrowing_detail),
    path("api/returns/", api_data.returns_list),
    path("api/receipts/<str:ref_id>/", api_data.receipt_detail),
    path("api/notifications/", api_data.notifications_list),
    path("api/notifications/read-all/", api_data.notifications_read_all),
    path("api/notifications/<int:pk>/read/", api_data.notification_read),
]