"""Pages (HTML). All data is loaded by the JavaScript through the /api/ endpoints."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from .api_auth import read_verify_token, reset_session_valid
from .models import Category, NotificationType
from .notifications import create_notification


def _guest_page(template):
    @ensure_csrf_cookie
    def view(request):
        if request.user.is_authenticated:
            return redirect("staff_dashboard" if request.user.is_staff else "dashboard")
        return render(request, template)

    return view


login_page = _guest_page("login.html")
create_account_page = _guest_page("create_account.html")
reset_password_page = _guest_page("reset_password.html")


@ensure_csrf_cookie
def change_password_page(request):
    # Only reachable after a reset code was verified on the server.
    if not reset_session_valid(request):
        return redirect("reset_password")
    return render(request, "change_password.html")


def verify_email(request, token):
    user = read_verify_token(token)
    if not user:
        return redirect("/login/?verify_error=1")
    if not user.is_email_verified:
        user.is_email_verified = True
        user.save(update_fields=["is_email_verified"])
        create_notification(
            user, NotificationType.ACCOUNT_VERIFIED,
            "Your account has been successfully verified. You can now use REUSE.",
            channels=("web",),
        )
    return redirect("/login/?verified=1")


def _app_page(template):
    @login_required
    @ensure_csrf_cookie
    def view(request):
        if request.user.is_staff:
            return redirect("staff_dashboard")
        return render(request, template, {"categories": Category.choices})

    return view


dashboard_page = _app_page("dashboard.html")
browse_page = _app_page("browse.html")
requests_page = _app_page("my_requests.html")
borrowed_page = _app_page("borrowed.html")
returns_page = _app_page("items_to_return.html")
profile_page = _app_page("profile.html")
notifications_page = _app_page("notifications.html")
