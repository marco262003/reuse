"""
Face login rules. After a correct password the session only holds a pending sign-in. It becomes
a real login when the live face passes the head-turn challenge and matches the stored template,
or when the emailed fallback code is entered. Matching happens here on the server; the stored
template is never sent to the browser, and camera frames are never stored.
"""
import base64
import hashlib
import hmac
import io
import logging
import secrets
import time
from datetime import timedelta

import cv2
import numpy as np
from django.conf import settings
from django.contrib.auth import login
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from PIL import Image

from . import idscan
from .idscan import faces, liveness
from .models import NotificationType, PasswordResetCode, User, VerificationKind as Kind, VerificationOutcome as Outcome
from .notifications import create_notification
from .registration import client_ip, record
from .services import ServiceError

log = logging.getLogger(__name__)

SESSION_KEY = "pending_login"
FAILED = (Outcome.NO_MATCH, Outcome.LIVENESS_FAILED)
MAX_FRAME_BYTES = 400 * 1024
MAX_FRAME_SIDE = 800


# ---------------------------------------------------------------------------
# The pending sign-in
# ---------------------------------------------------------------------------
def begin(request, user):
    """Called when the password was right. Nobody is logged in yet."""
    request.session.cycle_key()
    request.session[SESSION_KEY] = {"uid": user.pk, "exp": time.time() + settings.PENDING_LOGIN_SECONDS, "unusable": 0}


def pending_user(request):
    state = request.session.get(SESSION_KEY)
    if not state:
        return None
    if state.get("exp", 0) < time.time():
        del request.session[SESSION_KEY]
        return None
    return User.objects.filter(pk=state.get("uid"), is_active=True).first()


def complete(request, user):
    """Turn the pending sign-in into a real login and say where to go."""
    request.session.pop(SESSION_KEY, None)
    login(request, user)  # rotates the session id
    return "/admin-dashboard/" if user.is_superuser else "/dashboard/"


def has_usable_template(user):
    """True when the account has a face template made by the current engine and readable with the current key."""
    return (
        bool(user.face_template)
        and user.face_model == idscan.face_provider_name()
        and faces.unseal(user.face_template) is not None
    )


def failed_scans(user):
    """Failed scans in the pause window since the last successful one."""
    since = timezone.now() - timedelta(minutes=settings.FACE_PAUSE_MINUTES)
    records = user.verification_records.filter(kind=Kind.FACE_LOGIN, created_at__gte=since)
    last_success = records.filter(outcome=Outcome.SUCCESS).first()
    if last_success:
        records = records.filter(id__gt=last_success.id)
    return records.filter(outcome__in=FAILED).count()


def attempts_left(user):
    return max(0, settings.FACE_MAX_FAILED_SCANS - failed_scans(user))


def next_step(user):
    """What someone with a correct password must do next: done, enroll_id, email_code or face_scan."""
    if not has_usable_template(user):
        if user.is_superuser and settings.FACE_LOGIN_EXEMPT_SUPERUSERS:
            return "done"
        return "enroll_id"
    return "face_scan" if attempts_left(user) else "email_code"


# ---------------------------------------------------------------------------
# Face scan
# ---------------------------------------------------------------------------
def start_challenge(request, user):
    challenge = {
        "id": secrets.token_urlsafe(16),
        "steps": liveness.new_challenge(settings.LIVENESS_TURN_STEPS),
        "exp": time.time() + settings.LIVENESS_CHALLENGE_SECONDS,
    }
    request.session[SESSION_KEY] = {**request.session[SESSION_KEY], "challenge": challenge}
    return {
        "challenge_id": challenge["id"], "steps": challenge["steps"],
        "frames_per_step": settings.LIVENESS_FRAMES_PER_STEP, "expires_in": settings.LIVENESS_CHALLENGE_SECONDS,
        "attempts_left": attempts_left(user),
    }


def _decode_frame(data_url):
    """A camera frame from the browser as a BGR image, or None when it is not a usable JPG or PNG."""
    head, _, body = str(data_url or "").partition(",")
    if not head.startswith("data:image/") or len(body) > MAX_FRAME_BYTES * 4 // 3 + 4:
        return None
    try:
        image = Image.open(io.BytesIO(base64.b64decode(body, validate=True)))
        if image.format not in ("JPEG", "PNG"):
            return None
        image = image.convert("RGB")
        image.thumbnail((MAX_FRAME_SIDE, MAX_FRAME_SIDE))
    except Exception:  # noqa: BLE001 - Pillow raises many types for a damaged image
        return None
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def _failed(request, user, outcome, similarity=None):
    record(Kind.FACE_LOGIN, outcome, user=user, ip=client_ip(request), similarity=similarity)
    return "not_confirmed", attempts_left(user)


def _unusable(request, user, outcome):
    """No face, or several: the person may retake without losing a try, up to a limit per sign-in."""
    state = request.session[SESSION_KEY]
    request.session[SESSION_KEY] = {**state, "unusable": state.get("unusable", 0) + 1}
    if state.get("unusable", 0) >= settings.FACE_UNUSABLE_SCANS_PER_SIGN_IN:
        return _failed(request, user, Outcome.LIVENESS_FAILED)
    record(Kind.FACE_LOGIN, outcome, user=user, ip=client_ip(request))
    return outcome.value, attempts_left(user)


def verify(request, user, challenge_id, frames):
    """
    Check the frames captured for a challenge. Returns (result, attempts_left) where result is
    success, not_confirmed, no_face, multiple_faces, challenge_expired or paused.
    """
    state = request.session[SESSION_KEY]
    challenge = state.get("challenge")
    request.session[SESSION_KEY] = {key: value for key, value in state.items() if key != "challenge"}  # single use
    if not challenge or not hmac.compare_digest(challenge["id"], challenge_id) or challenge["exp"] < time.time():
        return "challenge_expired", attempts_left(user)

    with transaction.atomic():
        User.objects.select_for_update().filter(pk=user.pk).first()  # one scan at a time per account
        if not attempts_left(user):
            return "paused", 0
        return _judge(request, user, challenge["steps"], frames)


def _judge(request, user, steps, frames):
    provider = idscan.face_provider()
    by_step = [[] for _ in steps]  # (image, face) for every usable frame of each step
    for frame in frames[: len(steps) * settings.LIVENESS_FRAMES_PER_STEP * 2]:
        step = frame.get("step") if isinstance(frame, dict) else None
        if not isinstance(step, int) or isinstance(step, bool) or not 0 <= step < len(steps):
            continue
        image = _decode_frame(frame.get("image"))
        if image is None:
            continue
        min_width = image.shape[1] * settings.SELFIE_FACE_MIN_SHARE
        found = [face for face in provider.detect(image) if face.width >= min_width]
        if len(found) > 1:
            return _unusable(request, user, Outcome.MULTIPLE_FACES)
        if found:
            by_step[step].append((image, found[0]))
    if not all(by_step):
        return _unusable(request, user, Outcome.NO_FACE)

    # Liveness: the head really turned the way the challenge asked...
    turns = [[liveness.turn(face) or 0.0 for _, face in pairs] for pairs in by_step]
    verdict = liveness.judge(steps, turns, settings.LIVENESS_TURN_MIN, settings.LIVENESS_CENTER_MAX)
    if not verdict.passed:
        return _failed(request, user, Outcome.LIVENESS_FAILED)
    templates = [provider.template(*by_step[i][pick]) for i, pick in enumerate(verdict.picks)]
    straight = [template for step, template in zip(steps, templates) if step == liveness.CENTER]
    # ...and it was one person throughout, not a photo for the straight-on frames and someone else turning.
    if any(faces.similarity(straight[0], other) < settings.LIVENESS_SAME_PERSON_MIN for other in templates):
        return _failed(request, user, Outcome.LIVENESS_FAILED)

    # Match: the live face against the template made from the ID portrait.
    stored = faces.unseal(user.face_template)
    score = max(faces.similarity(template, stored) for template in straight)
    if score < settings.FACE_MATCH_THRESHOLD:
        return _failed(request, user, Outcome.NO_MATCH, similarity=score)
    record(Kind.FACE_LOGIN, Outcome.SUCCESS, user=user, ip=client_ip(request), similarity=score)
    return "success", settings.FACE_MAX_FAILED_SCANS


# ---------------------------------------------------------------------------
# Fallback: a one-time code sent to the school email
# ---------------------------------------------------------------------------
def _code_hash(user, code):
    return hmac.new(settings.SECRET_KEY.encode(), f"login:{user.pk}:{code}".encode(), hashlib.sha256).hexdigest()


def send_code(user):
    """Email a sign-in code. Returns 0, or the seconds to wait when one was sent too recently."""
    now = timezone.now()
    recent = PasswordResetCode.objects.filter(user=user, purpose="login", created_at__gte=now - timedelta(hours=1))
    last = recent.first()  # newest first
    if last:
        wait = settings.RESET_RESEND_COOLDOWN_SECONDS - (now - last.created_at).total_seconds()
        if wait > 0:
            return int(wait) + 1
    if recent.count() >= settings.RESET_MAX_SENDS_PER_HOUR:
        return 3600
    code = f"{secrets.randbelow(10 ** 6):06d}"
    row = PasswordResetCode.objects.create(
        user=user, channel="email", purpose="login", code_hash=_code_hash(user, code),
        expires_at=now + timedelta(seconds=settings.RESET_CODE_TTL_SECONDS),
    )
    minutes = settings.RESET_CODE_TTL_SECONDS // 60
    try:
        send_mail(
            "Your REUSE sign-in code",
            f"Your REUSE sign-in code is {code}. It expires in {minutes} minutes. Don't share it with anyone.",
            settings.DEFAULT_FROM_EMAIL, [user.email],
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Could not send sign-in code to user %s", user.pk)
        row.delete()
        raise ServiceError("We couldn't send the code. Please try again in a moment.", 503) from exc
    return 0


def check_code(request, user, code):
    """True when the code is right. The account owner is told that a code was used instead of a face scan."""
    ip = client_ip(request)
    row = PasswordResetCode.objects.filter(user=user, purpose="login", consumed=False).first()
    if not row or row.expires_at < timezone.now() or row.attempts >= settings.RESET_MAX_ATTEMPTS:
        return False
    PasswordResetCode.objects.filter(pk=row.pk).update(attempts=F("attempts") + 1)
    if not hmac.compare_digest(row.code_hash, _code_hash(user, code)):
        record(Kind.EMAIL_CODE, Outcome.WRONG_CODE, user=user, ip=ip)
        return False
    if not PasswordResetCode.objects.filter(pk=row.pk, consumed=False).update(consumed=True):
        return False  # used a split second earlier
    record(Kind.EMAIL_CODE, Outcome.SUCCESS, user=user, ip=ip)
    create_notification(
        user, NotificationType.SIGN_IN_ALERT,
        "A sign-in to your account used an email code instead of a face scan. If this wasn't you, change your password.",
        channels=("web", "email"),
    )
    return True
