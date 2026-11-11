"""
ID-first registration rules: a draft for each sign-up in progress, the uploaded school ID,
what was read from it, and the account that results. The JSON API stays thin and goes through here.
"""
import hashlib
import logging
import re
import uuid
from datetime import date, timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from . import idscan, names, registration_fields as rf, validators as v
from .idscan import faces, mapping, preprocess, storage
from .models import (
    IDDocument, IDSide, RegistrationDraft, User, UserType, VerificationKind as Kind,
    VerificationOutcome as Outcome, VerificationRecord,
)
from .services import ServiceError, audit

log = logging.getLogger(__name__)

SESSION_KEY = "registration_draft"
ID_NUMBER_RE = re.compile(r"^[A-Z0-9-]{4,20}$")

NO_FACE = "We couldn't read the photo on your ID. Please retake it in good light, without glare."
BAD_FILE = "That file isn't a JPG, PNG or PDF we can read. Please choose another."
DUPLICATE_ID = "This ID has already been used to register. Log in instead, or contact the administrator."
EXPIRED = "Your ID upload has expired. Please upload your school ID again."
NOT_SET_UP = "ID upload isn't set up on this server yet. Please contact the administrator."
UNAVAILABLE = "We couldn't reach the ID upload service. Please try again in a moment."
NO_TEXT = "We couldn't read the text on your ID. Please type your details."
SOME_TEXT = "We couldn't read some details. Please check the form and type what's missing."
CHECK_NAME = "Please check that your name is split correctly."
OUTCOME_FOR_CODE = {"no_face_on_id": Outcome.NO_FACE, "duplicate_id": Outcome.DUPLICATE, "invalid_file": Outcome.BAD_FILE}


def client_ip(request):
    return request.META.get("REMOTE_ADDR") or None


def record(kind, outcome, *, user=None, draft=None, ip=None, similarity=None):
    """Write one line of the verification log. Scores only: never images or face templates."""
    VerificationRecord.objects.create(
        kind=kind, outcome=outcome, user=user, draft_id=draft.pk if draft else None, ip=ip, similarity=similarity,
    )


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------
def current_draft(request, create=False):
    """The visitor's sign-up in progress. Holding its id in the session is what authorises the ID calls."""
    draft = None
    try:
        draft = RegistrationDraft.objects.filter(
            pk=request.session.get(SESSION_KEY), expires_at__gt=timezone.now(),
        ).first()
    except (ValidationError, ValueError):  # not a UUID
        pass
    if draft is None and create:
        draft = RegistrationDraft.objects.create(
            expires_at=timezone.now() + timedelta(seconds=settings.REGISTRATION_DRAFT_SECONDS), ip=client_ip(request),
        )
        request.session[SESSION_KEY] = str(draft.pk)
    return draft


def _discard(documents):
    """Delete uploaded files and their rows. A storage failure is left to the bucket's lifecycle rule."""
    for document in documents:
        if not document.image_deleted_at:
            try:
                storage.delete(document.storage_key)
            except storage.StorageUnavailable:
                pass
        document.delete()


def discard_id_images(user):
    """Forget an account's ID documents, with any images still kept for them."""
    _discard(user.id_documents.all())


def purge_expired_drafts():
    """Remove sign-ups that were never finished, with their uploaded files."""
    count = 0
    for draft in RegistrationDraft.objects.filter(expires_at__lt=timezone.now()):
        _discard(draft.documents.all())
        draft.delete()
        count += 1
    return count


# ---------------------------------------------------------------------------
# Step 1: upload the ID
# ---------------------------------------------------------------------------
def start_upload(request, side, content_type, size):
    """Check the declared file and return (document, pre-signed upload)."""
    if not storage.is_configured():
        raise ServiceError(NOT_SET_UP, 503)
    errors = {}
    megabytes = settings.ID_UPLOAD_MAX_BYTES // (1024 * 1024)
    if side not in IDSide.values:
        errors["side"] = "Choose the front or the back of the ID."
    if content_type not in settings.ID_UPLOAD_TYPES:
        errors["file"] = "Upload a JPG, PNG or PDF."
    elif not isinstance(size, int) or isinstance(size, bool) or size < 1:
        errors["file"] = "That file is empty."
    elif size > settings.ID_UPLOAD_MAX_BYTES:
        errors["file"] = f"File must be {megabytes} MB or smaller."
    if errors:
        raise ServiceError("Please choose a different file.", errors=errors)

    draft = current_draft(request, create=True)
    if draft.uploads >= settings.ID_UPLOADS_PER_DRAFT:
        raise ServiceError("Too many uploads. Please try again later.", 429)
    # Uploading a side again replaces it. A new front is a new card, so its old back goes too.
    _discard(draft.documents.all() if side == IDSide.FRONT else draft.documents.filter(side=side))
    key =f"pending/{draft.pk}/id/{side}-{uuid.uuid4().hex}.{settings.ID_UPLOAD_TYPES[content_type]}"
    document = IDDocument.objects.create(draft=draft, side=side, storage_key=key, content_type=content_type, size_bytes=size)
    draft.uploads += 1
    if side == IDSide.FRONT:
        draft.face_template, draft.face_model = None, ""  # a new front voids the earlier portrait
    draft.save(update_fields=["uploads", "face_template", "face_model"])
    try:
        return document, storage.presigned_upload(key, content_type)
    except storage.StorageUnavailable as exc:
        raise ServiceError(UNAVAILABLE, 503) from exc


# ---------------------------------------------------------------------------
# Step 2: read the ID
# ---------------------------------------------------------------------------
def _limit_extractions(draft, ip):
    if draft.extractions >= settings.ID_EXTRACTS_PER_DRAFT:
        raise ServiceError("Too many tries with this ID. Please try again later.", 429)
    key = f"id-extract:{ip}"
    cache.add(key, 0, 60 * 60)
    if cache.incr(key) > settings.ID_EXTRACTS_PER_IP_PER_HOUR:
        raise ServiceError("Too many ID uploads from this network. Please try again later.", 429)
    RegistrationDraft.objects.filter(pk=draft.pk).update(extractions=F("extractions") + 1)


def _read_upload(document):
    """The uploaded file's bytes, checked by size and by signature (not by its name or declared type)."""
    try:
        data = storage.fetch(document.storage_key)
    except storage.FileTooLarge as exc:
        raise ServiceError(BAD_FILE, 400, code="invalid_file") from exc
    except storage.StorageUnavailable as exc:
        raise ServiceError(UNAVAILABLE, 503) from exc
    if data is None:
        raise ServiceError("Your ID photo didn't arrive. Please upload it again.", 404, code="upload_missing")
    if len(data) > settings.ID_UPLOAD_MAX_BYTES or preprocess.sniff_type(data) not in settings.ID_UPLOAD_TYPES:
        raise ServiceError(BAD_FILE, 400, code="invalid_file")
    return data


def _find_portrait(image, provider):
    """
    Try the picture as it is and at each quarter turn, and keep the turn where the detector is
    most sure of the portrait. A sideways or upside-down face is still "found", but with a lower
    score, and it gives a useless template. Returns (upright image, face) or (image, None).
    """
    best = None
    for turns in range(4):
        turned = preprocess.quarter_turns(image, turns) if turns else image
        usable = [face for face in provider.detect(turned)
                  if face.upright and face.width >= settings.ID_FACE_MIN_PIXELS]
        if usable and (best is None or usable[0].score > best[1].score):
            best = (turned, usable[0])
    return best or (image, None)


def _read_text(image):
    """OCR and field mapping. Any failure gives an empty result: unreadable text never blocks registration."""
    try:
        lines = idscan.ocr_provider().read(preprocess.prepare_for_ocr(image))
        return mapping.map_fields(lines, idscan.id_template())
    except Exception:  # noqa: BLE001
        log.exception("Could not read the text on an ID")
        return mapping.Extraction()


def _read_back(document):
    try:
        return _read_text(preprocess.load_image(_read_upload(document)))
    except (ServiceError, preprocess.UnreadableFile):
        return mapping.Extraction()  # the back is optional


def _refuse_registered_image(digest, owner=None):
    taken = IDDocument.objects.filter(sha256=digest, side=IDSide.FRONT, user__isnull=False)
    if owner is not None:
        taken = taken.exclude(user=owner)
    if digest and taken.exists():
        raise ServiceError(DUPLICATE_ID, 409, code="duplicate_id")


def extract(request, owner=None):
    """
    Read the uploaded ID: keep the portrait's face template on the draft and return the
    ID-derived fields that could be read. owner is the account adding its own ID, if any.
    """
    draft = current_draft(request)
    front = draft.documents.filter(side=IDSide.FRONT).first() if draft else None
    if front is None:
        raise ServiceError("Upload a photo of your school ID first.", 404, code="upload_missing")
    ip = client_ip(request)
    _limit_extractions(draft, ip)
    try:
        result = _extract(draft, front, owner)
    except ServiceError as exc:
        record(Kind.ID_EXTRACT, OUTCOME_FOR_CODE.get(exc.code, Outcome.ERROR), user=owner, draft=draft, ip=ip)
        raise
    record(Kind.ID_EXTRACT, Outcome.SUCCESS, user=owner, draft=draft, ip=ip)
    return result


def _extract(draft, front, owner):
    data = _read_upload(front)
    front.sha256, front.content_type, front.size_bytes = hashlib.sha256(data).hexdigest(), preprocess.sniff_type(data), len(data)
    front.save(update_fields=["sha256", "content_type", "size_bytes"])
    _refuse_registered_image(front.sha256, owner)
    try:
        image = preprocess.load_image(data)
    except preprocess.UnreadableFile as exc:
        raise ServiceError(BAD_FILE, 400, code="invalid_file") from exc
    try:
        provider = idscan.face_provider()
    except faces.FaceEngineUnavailable as exc:
        log.error("%s", exc)
        raise ServiceError("Face reading isn't set up on this server yet. Please contact the administrator.", 503) from exc

    image, face = _find_portrait(image, provider)
    if face is None:
        raise ServiceError(NO_FACE, 422, code="no_face_on_id")
    draft.face_template, draft.face_model = faces.seal(provider.template(image, face)), provider.name
    draft.save(update_fields=["face_template", "face_model"])

    extraction = _read_text(image)
    back = draft.documents.filter(side=IDSide.BACK).first()
    if back:
        for name, value in _read_back(back).fields.items():
            extraction.fields.setdefault(name, value)
    fields = rf.only_id_derived(extraction.fields)

    warnings = []
    if not fields:
        warnings.append(NO_TEXT)
    elif any(f.name not in fields for f in rf.FIELDS if f.source == rf.ID and f.required):
        warnings.append(SOME_TEXT)
    if extraction.name_low_confidence:
        warnings.append(CHECK_NAME)

    front.raw_name, front.name_format, front.face_found = extraction.raw_name, extraction.name_format, True
    front.field_confidence = {name: value["confidence"] for name, value in fields.items()}
    front.ocr_engine = idscan.ocr_provider().name if fields else ""
    front.save(update_fields=["raw_name", "name_format", "face_found", "field_confidence", "ocr_engine"])
    return {
        "face": {"found": True},
        "name": {"format": extraction.name_format, "low_confidence": extraction.name_low_confidence},
        "fields": fields,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Step 3: create the account
# ---------------------------------------------------------------------------
def _text(data, key):
    return re.sub(r"\s+", " ", str(data.get(key) or "")).strip()


def clean_registration(data):
    """Return (values, errors). The rules are the same whether a value was autofilled or typed."""
    values = {
        "first_name": names.clean_name_part(data.get("first_name")),
        "middle_name": names.clean_middle_name(data.get("middle_name")),
        "last_name": names.clean_name_part(data.get("last_name")),
        "suffix": names.clean_suffix(data.get("suffix")),
        "student_id_number": _text(data, "student_id_number").upper().replace(" ", ""),
        "program": _text(data, "program"),
        "year_level": _text(data, "year_level"),
        "college": _text(data, "college"),
        "date_of_birth": None,
        "sex": _text(data, "sex").lower(),
        "address": _text(data, "address"),
        "id_valid_until": _text(data, "id_valid_until"),
        "email": v.normalize_email(_text(data, "email")),
        "mobile": v.normalize_mobile(_text(data, "mobile")),
        "user_type": _text(data, "user_type"),
    }
    password = data.get("password") if isinstance(data.get("password"), str) else ""
    confirm = data.get("confirm_password") if isinstance(data.get("confirm_password"), str) else ""
    errors = {}

    for name, required in (("first_name", True), ("last_name", True), ("middle_name", False)):
        if err := names.check_name_part(values[name], required):
            errors[name] = err
    if err := names.check_suffix(values["suffix"]):
        errors["suffix"] = err

    if not values["student_id_number"]:
        errors["student_id_number"] = v.REQUIRED
    elif not ID_NUMBER_RE.match(values["student_id_number"]):
        errors["student_id_number"] = "Enter the number printed on your school ID (letters, numbers and hyphens)."
    elif User.objects.filter(student_id_number=values["student_id_number"]).exists():
        errors["student_id_number"] = "This ID number is already registered. Log in instead, or contact the administrator."

    if not values["user_type"]:
        errors["user_type"] = v.REQUIRED
    elif values["user_type"] not in UserType.values:
        errors["user_type"] = "Select Student, Faculty or Staff."

    for name in ("program", "year_level", "college", "address", "id_valid_until"):
        spec = rf.BY_NAME[name]
        if not values[name] and rf.is_required(spec, values["user_type"]):
            errors[name] = v.REQUIRED
        elif spec.max_length and len(values[name]) > spec.max_length:
            errors[name] = f"Use {spec.max_length} characters or fewer."
    if values["year_level"] and values["year_level"] not in dict(rf.YEAR_LEVELS):
        errors["year_level"] = "Select a year level."
    if values["sex"] and values["sex"] not in dict(rf.SEXES):
        errors["sex"] = "Select Male or Female, or leave this blank."

    if born := _text(data, "date_of_birth"):
        try:
            values["date_of_birth"] = date.fromisoformat(born)
            if not date(1900, 1, 1) <= values["date_of_birth"] <= date.today():
                raise ValueError
        except ValueError:
            errors["date_of_birth"] = "Enter a valid date of birth."

    if err := v.check_email(values["email"]):
        errors["email"] = err
    elif User.objects.filter(email__iexact=values["email"]).exists():
        errors["email"] = "This email is already registered. Log in instead."
    if not _text(data, "mobile"):
        errors["mobile"] = v.REQUIRED
    elif not values["mobile"]:
        errors["mobile"] = "Enter a valid Philippine mobile number (+63 9XX XXX XXXX)."
    elif User.objects.filter(mobile=values["mobile"]).exists():
        errors["mobile"] = "This mobile number is already registered."

    if err := v.check_password(password):
        errors["password"] = err
    if not confirm:
        errors["confirm_password"] = v.REQUIRED
    elif password != confirm:
        errors["confirm_password"] = "Passwords do not match."
    if data.get("consent") is not True:
        errors["consent"] = "Please agree before continuing."
    return values, errors


def _draft_with_portrait(request, owner=None):
    draft = current_draft(request)
    if draft is None or not draft.face_template:
        raise ServiceError(EXPIRED, 409, code="no_reference_face")
    front = draft.documents.filter(side=IDSide.FRONT).first()
    _refuse_registered_image(front.sha256 if front else "", owner)
    return draft


def release_id_images(user):
    """Once the account exists: delete its ID images, or keep them under its own path for the retention period."""
    for document in user.id_documents.filter(image_deleted_at__isnull=True, storage_key__startswith="pending/"):
        try:
            if settings.ID_IMAGE_RETENTION_DAYS > 0:
                kept = f"users/{user.pk}/id/{document.storage_key.rsplit('/', 1)[-1]}"
                storage.move(document.storage_key, kept)
                document.storage_key = kept
            else:
                storage.delete(document.storage_key)
                document.image_deleted_at = timezone.now()
            document.save(update_fields=["storage_key", "image_deleted_at"])
        except storage.StorageUnavailable:
            log.error("Could not release ID image %s; the bucket's lifecycle rule will remove it.", document.storage_key)


def register(request, data):
    """Create the account from exactly the values sent: the person's edits always win."""
    values, errors = clean_registration(data)
    if errors:
        raise ServiceError("Please fix the highlighted fields.", errors=errors)
    draft = _draft_with_portrait(request)
    now = timezone.now()
    try:
        with transaction.atomic():
            user = User.objects.create_user(
                password=data["password"], face_template=bytes(draft.face_template), face_model=draft.face_model,
                face_enrolled_at=now, consent_at=now, **values,
            )
            draft.documents.update(user=user, draft=None)
            draft.delete()
    except IntegrityError as exc:
        # Someone took a unique value a moment ago. Checking again says which one.
        _, errors = clean_registration(data)
        if errors:
            raise ServiceError("Please fix the highlighted fields.", errors=errors) from exc
        raise ServiceError(DUPLICATE_ID, 409, code="duplicate_id") from exc
    request.session.pop(SESSION_KEY, None)
    release_id_images(user)
    audit(user, "account_registered", user)
    return user


def enroll(request, user, consent):
    """Give an existing account (older, or created by an administrator) its reference face."""
    if consent is not True:
        raise ServiceError("Please agree before continuing.", errors={"consent": "Please agree before continuing."})
    draft = _draft_with_portrait(request, owner=user)
    now = timezone.now()
    with transaction.atomic():
        user.id_documents.all().delete()  # an earlier ID is replaced
        user.face_template, user.face_model = bytes(draft.face_template), draft.face_model
        user.face_enrolled_at = user.consent_at = now
        user.save(update_fields=["face_template", "face_model", "face_enrolled_at", "consent_at"])
        draft.documents.update(user=user, draft=None)
        draft.delete()
    request.session.pop(SESSION_KEY, None)
    release_id_images(user)
    audit(user, "face_enrolled", user)
