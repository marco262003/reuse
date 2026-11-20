"""
The single source of truth for registration fields: which may be autofilled from the school
ID ("id") and which only the person may type ("manual"). The form, the autofill script and
the extraction endpoint all read this list; nothing else decides a field's category.
"""
from dataclasses import asdict, dataclass

from .names import is_placeholder

ID = "id"
MANUAL = "manual"

YEAR_LEVELS = (("1", "1st Year"), ("2", "2nd Year"), ("3", "3rd Year"), ("4", "4th Year"), ("5", "5th Year"), ("6", "6th Year"))
SEXES = (("male", "Male"), ("female", "Female"))
USER_TYPES = (("student", "Student"), ("faculty", "Faculty"), ("staff", "Staff"))


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    source: str = MANUAL      # a field is manual unless it is declared ID-derived here
    required: bool = False
    required_for: tuple = ()  # user types for which it is also required
    kind: str = "text"        # text | date | select | email | tel | password | checkbox
    max_length: int = 0
    choices: tuple = ()
    autocomplete: str = "off"


FIELDS = (
    # --- Printed on the school ID: autofilled when OCR reads them, always editable ---
    Field("last_name", "Last name", ID, required=True, max_length=60),
    Field("first_name", "First name", ID, required=True, max_length=60),
    Field("middle_name", "Middle name (optional)", ID, max_length=60),
    Field("suffix", "Suffix (optional)", ID, max_length=10),
    Field("student_id_number", "School ID number", ID, required=True, max_length=20),
    Field("program", "Course / program", ID, required_for=("student",), max_length=120),
    Field("year_level", "Year level", ID, required_for=("student",), kind="select", choices=YEAR_LEVELS),
    Field("college", "College / department (optional)", ID, max_length=120),
    Field("date_of_birth", "Date of birth (optional)", ID, kind="date"),
    Field("sex", "Sex (optional)", ID, kind="select", choices=SEXES),
    Field("address", "Address (optional)", ID, max_length=255),
    Field("id_valid_until", "ID valid until (optional)", ID, max_length=30),
    # --- Never autofilled, overwritten or cleared ---
    Field("email", "School email", MANUAL, required=True, kind="email", autocomplete="username"),
    Field("mobile", "Mobile number", MANUAL, required=True, kind="tel", autocomplete="tel-national"),
    Field("user_type", "User type", MANUAL, required=True, kind="select", choices=USER_TYPES),
    Field("password", "Password", MANUAL, required=True, kind="password", autocomplete="new-password"),
    Field("confirm_password", "Confirm password", MANUAL, required=True, kind="password", autocomplete="new-password"),
    Field("consent", "Consent", MANUAL, required=True, kind="checkbox"),
)

BY_NAME = {f.name: f for f in FIELDS}
ID_DERIVED = frozenset(f.name for f in FIELDS if f.source == ID)
MANUAL_ONLY = frozenset(f.name for f in FIELDS if f.source != ID)


def only_id_derived(values):
    """
    The autofill filter. Keeps ID-derived fields that really have a value, so a manual field
    can never be autofilled and a missing value never arrives as "", "null" or "N/A".
    """
    kept = {}
    for name, value in values.items():
        text = str(value.get("value") if isinstance(value, dict) else value or "").strip()
        if name in ID_DERIVED and text and text.lower() not in ("null", "undefined") and not _is_blank(name, text):
            kept[name] = value
    return kept


def _is_blank(name, text):
    # Dates and ID numbers have no letters, so the name placeholder rule only fits text fields.
    return BY_NAME[name].kind == "text" and name != "student_id_number" and is_placeholder(text)


def is_required(field, user_type):
    return field.required or user_type in field.required_for


def schema():
    return [asdict(f) for f in FIELDS]
