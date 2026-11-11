"""
Tunable values for ID-first registration and face login, all in one place.
settings.py loads this file after .env, so secrets and per-machine values come from the environment.
"""
import os
import sys
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
_DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"


# --- Uploaded ID files ----------------------------------------------------------
ID_UPLOAD_TYPES = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}
ID_UPLOAD_MAX_BYTES = 8 * 1024 * 1024
# 0 deletes the ID image as soon as registration succeeds. A higher number keeps it under
# users/{userId}/id/ for that many days (add a matching lifecycle rule to the bucket).
ID_IMAGE_RETENTION_DAYS = 0
UPLOAD_URL_SECONDS = 5 * 60
REGISTRATION_DRAFT_SECONDS = 30 * 60
ID_UPLOADS_PER_DRAFT = 12
ID_EXTRACTS_PER_DRAFT = 10
# Per network address. Kept generous because a whole campus can share one address.
ID_EXTRACTS_PER_IP_PER_HOUR = 100


# --- Object storage (any S3-compatible service) ---------------------------------
ID_BUCKET = os.environ.get("ID_BUCKET", "")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "")  # empty = Amazon S3
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
LOCAL_STORAGE_URL = "http://127.0.0.1:9000"
if _DEBUG and not ID_BUCKET:
    # Development default: an in-memory stand-in that `runserver` starts by itself the first
    # time an ID is uploaded (core/idscan/local_storage.py). No second command is needed.
    ID_BUCKET, S3_ENDPOINT_URL = "reuse-id-uploads", LOCAL_STORAGE_URL
LOCAL_STORAGE_AUTOSTART = _DEBUG and "test" not in sys.argv


# --- Engines (swappable: see README) ---------------------------------------------
OCR_PROVIDER = "core.idscan.ocr.RapidOCRProvider"
FACE_PROVIDER = "core.idscan.faces.OpenCVFaceProvider"
FACE_MODEL_DIR = _BASE_DIR / "idscan_models"


# --- Face matching ----------------------------------------------------------------
# Cosine similarity needed to accept a face. 0.363 is the value OpenCV publishes for SFace.
# Higher = fewer wrong people accepted, more right people rejected. Tune on real ID cards.
FACE_MATCH_THRESHOLD = float(os.environ.get("FACE_MATCH_THRESHOLD", "0.363"))
ID_FACE_MIN_PIXELS = 60            # an ID portrait narrower than this is too small to use
SELFIE_FACE_MIN_SHARE = 0.15       # a face must fill this share of the camera frame's width
FACE_MAX_FAILED_SCANS = 3          # failed scans before the email-code fallback
FACE_PAUSE_MINUTES = 15            # how long those failures count, and face scan stays paused
FACE_UNUSABLE_SCANS_PER_SIGN_IN = 10
FACE_LOGIN_EXEMPT_SUPERUSERS = True  # command-line admin accounts without an ID skip the face scan
# Encrypts stored face templates. Set a dedicated value in production:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Empty = derived from DJANGO_SECRET_KEY. Changing either makes people add their ID again.
FACE_TEMPLATE_KEY = os.environ.get("FACE_TEMPLATE_KEY", "")


# --- Liveness (head-turn challenge) -----------------------------------------------
LIVENESS_TURN_STEPS = 2            # random left/right turns per challenge
LIVENESS_FRAMES_PER_STEP = 4
LIVENESS_CHALLENGE_SECONDS = 60
# The nose's distance from the midpoint between the eyes, as a share of the eye distance.
LIVENESS_TURN_MIN = 0.14           # at least this far for a "turn" (about 16 degrees)
LIVENESS_CENTER_MAX = 0.08         # at most this far for "look straight"
LIVENESS_SAME_PERSON_MIN = 0.30    # turned frames must still look like the straight-on frame


# --- Sessions (REUSE uses server-side sessions in an HttpOnly cookie, not JWTs) ----
SESSION_COOKIE_AGE = 60 * 60 * 8   # a login lasts 8 hours
PENDING_LOGIN_SECONDS = 5 * 60     # time allowed between the password and the face scan


# --- School ID templates ------------------------------------------------------------
# How to find each field on a school's card. "labels" are the words printed before (or
# above) a value. These are untuned guesses until tried on real cards; anything not found
# is simply left for the person to type.
ID_TEMPLATE = os.environ.get("ID_TEMPLATE", "batstateu")
ID_TEMPLATES = {
    "batstateu": {
        "name_format": "comma",                   # SURNAME, First Middle  ("no_comma" or "auto" also work)
        "student_id_pattern": r"\b\d{2}-\d{5}\b",  # SR-Code such as 21-12345
        "labels": {
            "name": ("student name", "name"),
            "student_id_number": ("sr-code", "sr code", "student number", "student no", "id number", "id no"),
            "program": ("program", "course"),
            "year_level": ("year level", "year"),
            "college": ("college", "department"),
            "date_of_birth": ("date of birth", "birthdate", "birthday", "dob"),
            "sex": ("sex", "gender"),
            "address": ("address",),
            "id_valid_until": ("valid until", "valid thru", "validity", "expires", "expiry"),
        },
        # Lines that are never a person's name.
        "ignore": (
            "batangas state university", "national engineering university", "republic of the philippines",
            "student", "faculty", "staff", "employee", "identification", "signature", "campus",
        ),
    },
}
