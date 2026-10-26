"""Login, registration, email verification and password reset endpoints."""
import hashlib
import hmac
import logging
import secrets
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login, logout, update_session_auth_hash
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.core.mail import send_mail
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from . import validators as v
from .api import api_view, fail, ok
from .models import Notification, PasswordResetCode, User, UserType
from .security import invalidate_user_sessions
from .services import audit
from .sms import SMSNotConfigured, send_sms

log = logging.getLogger(__name__)

VERIFY_SALT = "reuse-email-verify"
NOT_REGISTERED = "Email or mobile number is not registered."
INVALID_CODE = "Invalid or expired verification code."
RESET_SESSION_SECONDS = 10 * 60


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------
def make_verify_token(user):
    return signing.dumps({"uid": user.pk}, salt=VERIFY_SALT)


def read_verify_token(token):
    """Return the user for a valid, unexpired token, otherwise None."""
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=settings.EMAIL_VERIFY_MAX_AGE_SECONDS)
    except signing.BadSignature:  # includes SignatureExpired
        return None
    return User.objects.filter(pk=data.get("uid")).first()



def send_verification_email(user):
    link = f"{settings.SITE_URL}/verify-email/{make_verify_token(user)}/"

    print("DEBUG SITE_URL:", settings.SITE_URL)
    print("DEBUG VERIFICATION LINK:", link)

    body = (
        f"Hi {user.full_name},\n\n"
        "Welcome to REUSE. Please verify your school email by opening this link:\n"
        f"{link}\n\n"
        "The link works for 3 days. If you didn't create this account, ignore this email."
    )
    send_mail(
        "Verify your REUSE account",
        body,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
    )



@api_view(["POST"], auth=False)
def register(request):
    d = request.data
    full_name = v.normalize_name(d.get("full_name"))
    email = v.normalize_email(d.get("email"))
    mobile_raw = (d.get("mobile") or "").strip()
    mobile = v.normalize_mobile(mobile_raw)
    user_type = d.get("user_type")
    password = d.get("password") or ""
    confirm = d.get("confirm_password") or ""

    errors = {}
    if err := v.check_full_name(full_name):
        errors["full_name"] = err
    if err := v.check_email(email):
        errors["email"] = err
    elif User.objects.filter(email__iexact=email).exists():
        errors["email"] = "This email is already registered. Log in instead."
    if not mobile_raw:
        errors["mobile"] = v.REQUIRED
    elif not mobile:
        errors["mobile"] = "Enter a valid Philippine mobile number (+63 9XX XXX XXXX)."
    elif User.objects.filter(mobile=mobile).exists():
        errors["mobile"] = "This mobile number is already registered."
    if not user_type:
        errors["user_type"] = v.REQUIRED
    elif user_type not in UserType.values:
        errors["user_type"] = "Select Student, Faculty or Staff."
    if err := v.check_password(password):
        errors["password"] = err
    if not confirm:
        errors["confirm_password"] = v.REQUIRED
    elif password != confirm:
        errors["confirm_password"] = "Passwords do not match."
    if errors:
        return fail("Please fix the highlighted fields.", errors=errors)

    try:
        with transaction.atomic():
            
            user = User.objects.create_user(
            email=email,
            password=password,
            full_name=full_name,
            mobile=mobile,
            user_type=user_type,
            identity_verification_status="not_started",
            identity_verified=False,
        )
    except IntegrityError:
        # Re-check after the transaction has rolled back.
        if User.objects.filter(email__iexact=email).exists():
            return fail(
                "Please fix the highlighted fields.",
                errors={"email": "This email is already registered. Log in instead."},
            )

        if User.objects.filter(mobile=mobile).exists():
            return fail(
                "Please fix the highlighted fields.",
                errors={"mobile": "This mobile number is already registered."},
            )

        log.exception("Registration failed due to an unexpected database constraint.")
        return fail(
            "We couldn't create your account due to a database conflict. Please try again.",
            status=409,
        )

    try:
        send_verification_email(user)
    except Exception:  # noqa: BLE001
        log.exception("Verification email failed for %s", email)
        return ok({
            "email_sent": False,
            "message": "Your account was created, but we couldn't send the verification email. "
                       "Log in and choose \"Resend verification link\".",
        }, status=201)
    return ok({"email_sent": True, "message": "A verification link has been sent to your school email."}, status=201)


@api_view(["POST"], auth=False)
def resend_verification(request):
    email = v.normalize_email(request.data.get("email"))
    generic = {"message": "If this account is waiting for verification, we sent a new link to your school email."}
    if v.check_email(email):
        return ok(generic)
    key = f"resend-verify:{email}"
    if cache.get(key):
        return fail("Please wait a minute before asking for another link.", 429)
    cache.set(key, 1, 60)
    user = User.objects.filter(email__iexact=email, is_active=True, is_email_verified=False).first()
    if user:
        try:
            send_verification_email(user)
        except Exception:  # noqa: BLE001
            log.exception("Verification email failed for %s", email)
    return ok(generic)


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------
def _throttle_key(request, email):
    return f"login-fail:{request.META.get('REMOTE_ADDR', '?')}:{email}"


@api_view(["POST"], auth=False)
def login_view(request):
    email = v.normalize_email(request.data.get("email"))
    password = request.data.get("password") or ""

    errors = {}
    user = User.objects.filter(email__iexact=email).first() if email else None
    if user and user.is_superuser:
        pass
    elif err := v.check_email(email):
        errors["email"] = err
    if not password:
        errors["password"] = v.REQUIRED
    if errors:
        return fail("Please fix the highlighted fields.", errors=errors)

    key = _throttle_key(request, email)
    failures = cache.get(key, 0)
    if failures >= settings.LOGIN_MAX_FAILURES:
        return fail("Too many failed attempts. Try again in 15 minutes.", 429)

    if user is None:
        cache.set(key, failures + 1, 15 * 60)
        return fail("Email is not registered.", 401)
    if not user.check_password(password):
        cache.set(key, failures + 1, 15 * 60)
        return fail("Email or password is incorrect.", 401)
    if not user.is_active:
        return fail("This account has been deactivated. Please contact the administrator.", 403)
    if not user.is_email_verified:
        return fail("Account not verified. Please verify your email before logging in.", 403, code="unverified")

    cache.delete(key)
    login(request, user)  # rotates the session id
    
    if user.is_superuser:
        return ok({"redirect": "/admin-dashboard/"})

    return ok({"redirect": "/dashboard/"})



@api_view(["POST"])
def logout_view(request):
    logout(request)  # flushes the session on the server
    return ok({"redirect": "/login/"})


# ---------------------------------------------------------------------------
# Password reset: send code -> verify code -> change password
# ---------------------------------------------------------------------------
def _hash_code(user, code):
    return hmac.new(settings.SECRET_KEY.encode(), f"{user.pk}:{code}".encode(), hashlib.sha256).hexdigest()


def _find_user(identifier):
    """Return (channel, user, error_message)."""
    identifier = (identifier or "").strip()
    if not identifier:
        return None, None, v.REQUIRED
    if "@" in identifier:
        email = v.normalize_email(identifier)
        if err := v.check_email(email):
            return None, None, err
        return "email", User.objects.filter(email__iexact=email, is_active=True).first(), None
    mobile = v.normalize_mobile(identifier)
    if not mobile:
        return None, None, "Enter your registered mobile number in +63 format."
    return "sms", User.objects.filter(mobile=mobile, is_active=True).first(), None


def _send_reset_code(user, channel, code):
    minutes = settings.RESET_CODE_TTL_SECONDS // 60
    text = f"Your REUSE verification code is {code}. It expires in {minutes} minutes. Don't share it with anyone."
    if channel == "email":
        send_mail("Your REUSE verification code", text, settings.DEFAULT_FROM_EMAIL, [user.email])
        return
    try:
        send_sms(user.mobile, text)
    except SMSNotConfigured:
        if not settings.DEBUG:
            raise
        log.warning("DEV ONLY - SMS provider not configured. Reset code for %s is %s", user.mobile, code)


@api_view(["POST"], auth=False)
def reset_send_code(request):
    channel, user, err = _find_user(request.data.get("identifier"))
    if err:
        return fail(err, errors={"identifier": err})
    if not user:
        return fail(NOT_REGISTERED, 404, errors={"identifier": NOT_REGISTERED})

    now = timezone.now()
    recent = PasswordResetCode.objects.filter(user=user, created_at__gte=now - timedelta(hours=1))
    last = recent.first()  # newest first
    if last:
        wait = settings.RESET_RESEND_COOLDOWN_SECONDS - (now - last.created_at).total_seconds()
        if wait > 0:
            return fail("Please wait before requesting another code.", 429, retry_after=int(wait) + 1)
    if recent.count() >= settings.RESET_MAX_SENDS_PER_HOUR:
        return fail("Too many codes requested. Please try again later.", 429, retry_after=3600)

    code = f"{secrets.randbelow(10 ** 6):06d}"
    record = PasswordResetCode.objects.create(
        user=user, channel=channel, code_hash=_hash_code(user, code),
        expires_at=now + timedelta(seconds=settings.RESET_CODE_TTL_SECONDS),
    )
    try:
        _send_reset_code(user, channel, code)
    except Exception:  # noqa: BLE001
        log.exception("Could not send reset code to user %s", user.pk)
        record.delete()
        return fail("We couldn't send the code. Please try again in a moment.", 503)

    where = "your school email" if channel == "email" else "your mobile number"
    return ok({
        "channel": channel,
        "cooldown": settings.RESET_RESEND_COOLDOWN_SECONDS,
        "message": f"We sent a verification code to {where}.",
    })


@api_view(["POST"], auth=False)
def reset_verify_code(request):
    identifier = (request.data.get("identifier") or "").strip()
    code = str(request.data.get("code") or "").strip()
    errors = {}
    if not identifier:
        errors["identifier"] = v.REQUIRED
    if not code:
        errors["code"] = v.REQUIRED
    if errors:
        return fail("Please fill out the required fields.", errors=errors)

    channel, user, err = _find_user(identifier)
    if err:
        return fail(err, errors={"identifier": err})
    if not user:
        return fail(NOT_REGISTERED, 404, errors={"identifier": NOT_REGISTERED})

    invalid = fail(INVALID_CODE, 400, errors={"code": INVALID_CODE})
    record = PasswordResetCode.objects.filter(user=user, consumed=False).first()
    if not record or record.expires_at < timezone.now() or record.attempts >= settings.RESET_MAX_ATTEMPTS:
        return invalid
    PasswordResetCode.objects.filter(pk=record.pk).update(attempts=F("attempts") + 1)
    if not hmac.compare_digest(record.code_hash, _hash_code(user, code)):
        return invalid
    if not PasswordResetCode.objects.filter(pk=record.pk, consumed=False).update(consumed=True):
        return invalid  # someone used it a split second earlier

    # The server remembers who passed verification; the next step can't be skipped.
    request.session["reset_user_id"] = user.pk
    request.session["reset_expires"] = time.time() + RESET_SESSION_SECONDS
    return ok({"redirect": "/change-password/"})


def reset_session_valid(request):
    return bool(
        request.session.get("reset_user_id")
        and request.session.get("reset_expires", 0) > time.time()
    )


@api_view(["POST"], auth=False)
def reset_change_password(request):
    if not reset_session_valid(request):
        return fail("Your reset session expired. Please request a new code.", 403, redirect="/reset-password/")

    password = request.data.get("password") or ""
    confirm = request.data.get("confirm_password") or ""
    errors = {}
    if err := v.check_password(password):
        errors["password"] = err
    if not confirm:
        errors["confirm_password"] = v.REQUIRED
    elif password != confirm:
        errors["confirm_password"] = "Passwords do not match."
    if errors:
        return fail("Please fix the highlighted fields.", errors=errors)

    user = User.objects.filter(pk=request.session["reset_user_id"], is_active=True).first()
    if not user:
        return fail("Your reset session expired. Please request a new code.", 403, redirect="/reset-password/")
    user.set_password(password)
    user.save(update_fields=["password"])
    invalidate_user_sessions(user)
    request.session.pop("reset_user_id", None)
    request.session.pop("reset_expires", None)
    audit(user, "password_reset", user)
    return ok({"message": "Your password has been changed successfully.", "redirect": "/login/?pwchanged=1"})


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
@api_view(["GET"])
def me(request):
    from .serializers import user_dict

    unread = Notification.objects.filter(user=request.user, is_read=False).count()
    return ok({"user": user_dict(request.user), "unread_count": unread})




@login_required
@require_POST
def upload_profile_image(request):
    """Accept one validated profile photo upload for the authenticated user."""
    uploaded = request.FILES.get("image")
    if not uploaded:
        return fail("Choose a profile photo first.")
    if uploaded.size > 5 * 1024 * 1024:
        return fail("Profile photo must be 5 MB or smaller.")
    try:
        from PIL import Image as PILImage
        uploaded.seek(0)
        img = PILImage.open(uploaded)
        if img.format not in {"JPEG", "PNG", "WEBP"}:
            return fail("Upload a JPG, PNG, or WEBP image.")
        img.verify()
        uploaded.seek(0)
    except Exception:
        return fail("The uploaded file is not a valid image.")
    user = request.user
    if user.profile_image:
        user.profile_image.delete(save=False)
    user.profile_image = uploaded
    user.save(update_fields=["profile_image"])
    from .serializers import user_dict
    return ok({"message": "Profile photo updated.", "user": user_dict(user)})


@api_view(["POST"])
def change_password(request):
    current = request.data.get("current_password") or ""
    password = request.data.get("password") or ""
    confirm = request.data.get("confirm_password") or ""
    errors = {}
    if not current:
        errors["current_password"] = v.REQUIRED
    elif not request.user.check_password(current):
        errors["current_password"] = "Current password is incorrect."
    if err := v.check_password(password):
        errors["password"] = err
    if not confirm:
        errors["confirm_password"] = v.REQUIRED
    elif password != confirm:
        errors["confirm_password"] = "Passwords do not match."
    if errors:
        return fail("Please fix the highlighted fields.", errors=errors)

    request.user.set_password(password)
    request.user.save(update_fields=["password"])
    update_session_auth_hash(request, request.user)  # keep this session, drop the others
    audit(request.user, "password_changed", request.user)
    return ok({"message": "Password changed successfully."})
