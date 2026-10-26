"""Input rules shared by registration, login, password reset and profile."""
import re
import unicodedata

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@g\.batstate-u\.edu\.ph$", re.IGNORECASE)
# Letters only (ñ, é, ...). Words may be joined by one space, apostrophe or hyphen.
NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '\-][^\W\d_]+)*$")
MOBILE_RE = re.compile(r"^9\d{9}$")

REQUIRED = "Please fill out this field."
PASSWORD_HELP = "Use at least 8 characters, with an uppercase letter, a number and a special character."


def normalize_email(value):
    return (value or "").strip().lower()


def check_email(value):
    """Return an error message, or None when the email is a valid school email."""
    if not value:
        return REQUIRED
    if not EMAIL_RE.match(value):
        return "Use your school email (name@g.batstate-u.edu.ph)."
    return None


def normalize_name(value):
    value = unicodedata.normalize("NFC", (value or "").strip())
    value = value.replace("\u2019", "'")
    return re.sub(r"\s+", " ", value)


def check_full_name(value):
    if not value:
        return REQUIRED
    if not NAME_RE.match(value):
        return "Use letters only. Spaces, apostrophes and hyphens are allowed."
    if len(value.split(" ")) < 2:
        return "Enter your first name, middle name (if any) and last name."
    if len(value) > 150:
        return "Name is too long."
    return None


def normalize_mobile(value):
    """Return +639XXXXXXXXX or None when the number is not a valid PH mobile number."""
    digits = re.sub(r"[\s\-().]", "", value or "")
    if digits.startswith("+63"):
        digits = digits[3:]
    elif digits.startswith("63"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    return f"+63{digits}" if MOBILE_RE.match(digits) else None


def check_password(value):
    if not value:
        return REQUIRED
    if (
        len(value) < 8
        or not re.search(r"[A-Z]", value)
        or not re.search(r"\d", value)
        or not re.search(r"[^A-Za-z0-9]", value)
    ):
        return PASSWORD_HELP
    return None
