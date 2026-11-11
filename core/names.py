"""
Split a person's name, as printed on an ID, into separate parts, and check typed name parts.
Pure Python with no Django imports, so it can be tested and reused on its own.
"""
import re
import unicodedata
from dataclasses import dataclass, field

COMMA = "comma"        # SURNAME, FIRSTNAME MIDDLENAME
NO_COMMA = "no_comma"  # FIRSTNAME [MIDDLENAME] SURNAME
AUTO = "auto"          # use the comma when there is one

# Suffixes pulled out of a printed name. "V" is left out on purpose: after a comma it is far
# more often a middle initial. People can still type it (see SUFFIXES).
PARSED_SUFFIXES = {"JR": "Jr", "SR": "Sr", "II": "II", "III": "III", "IV": "IV"}
SUFFIXES = (*PARSED_SUFFIXES.values(), "V")

# Words that belong to the surname that follows them: DELA CRUZ, DE LA SALLE, VAN DER BERG.
SURNAME_PARTICLES = {
    "DE", "DEL", "DELA", "DELOS", "DELAS", "LA", "LOS", "LAS", "VAN", "VON", "DER", "DEN",
    "BIN", "BINTI", "SAN", "SANTA", "STA", "STO",
}
LOWERCASE_PARTICLES = {"BIN", "BINTI"}  # written in lowercase inside a name
PLACEHOLDERS = {"N/A", "NA", "NONE", "-", "."}

NAME_PART_RE = re.compile(r"^[^\W\d_]+(?:[ '\-][^\W\d_]+)*$")
SUFFIX_RE = re.compile(r"^[^\W_]+(?:[ .][^\W_]+)*\.?$")
MAX_NAME_LENGTH = 60
MAX_SUFFIX_LENGTH = 10

SURE = 1.0
GUESS = 0.5            # a split made without a comma to go by
LOW_CONFIDENCE = 0.6   # below this the form asks the person to check the value

REQUIRED = "Please fill out this field."
LETTERS_ONLY = "Use letters only. Spaces, apostrophes and hyphens are allowed."


@dataclass(frozen=True)
class ParsedName:
    raw: str
    format: str                                      # COMMA or NO_COMMA, as found on the card
    parts: dict = field(default_factory=dict)        # only the parts that were found
    confidence: dict = field(default_factory=dict)   # same keys as parts, 0 to 1

    @property
    def low_confidence(self):
        return any(score < LOW_CONFIDENCE for score in self.confidence.values())


def _normalize(value):
    text = unicodedata.normalize("NFC", str(value or "")).replace("’", "'").replace("`", "'")
    return re.sub(r"\s+", " ", text).strip()


def is_placeholder(value):
    """True for "N/A", "-", stray symbols and anything else that stands for "nothing here"."""
    text = _normalize(value)
    return text.upper() in PLACEHOLDERS or not any(ch.isalpha() for ch in text)


def _take_suffix(tokens):
    """Remove the first suffix token (never the first word) and hand its comma to the word before it."""
    for i, token in enumerate(tokens):
        key = token.strip(".,").upper()
        if i and key in PARSED_SUFFIXES:
            rest = tokens[:i] + tokens[i + 1:]
            if token.endswith(",") and not rest[i - 1].endswith(","):
                rest[i - 1] += ","
            return rest, PARSED_SUFFIXES[key]
    return tokens, ""


def _words(text):
    """Name words without their dots and commas; placeholders and stray symbols are dropped."""
    words = (token.strip(".,") for token in text.split())
    return [word for word in words if word.upper() != "N/A" and any(ch.isalpha() for ch in word)]


def _title(word):
    pieces = re.split(r"(['\-])", word)
    out = "".join(piece if piece in ("'", "-") else piece.capitalize() for piece in pieces)
    if out.startswith("Mc") and len(out) > 3:
        out = "Mc" + out[2:].capitalize()
    return out


def _display(words):
    """ALL-CAPS words become Title Case; anything already in mixed case is kept as printed."""
    shown = []
    for i, word in enumerate(words):
        if not word.isupper():
            shown.append(word)
        elif i and word in LOWERCASE_PARTICLES:
            shown.append(word.lower())
        else:
            shown.append(_title(word))
    return " ".join(shown)


def _split_comma(text):
    left, right = text.split(",", 1)
    given = _words(right.replace(",", " "))
    return {
        "last_name": (_words(left), SURE),
        "first_name": (given[:1], SURE if len(given) <= 2 else 0.8),
        "middle_name": (given[1:], 0.9 if len(given) == 2 else GUESS),
    }


def _split_given_first(words):
    start = len(words) - 1
    while start > 1 and words[start - 1].upper() in SURNAME_PARTICLES:
        start -= 1
    if len(words) < 2:
        start = len(words)
    return {
        "first_name": (words[:1], GUESS),
        "middle_name": (words[1:start], GUESS),
        "last_name": (words[start:], GUESS),
    }


def _split_surname_first(words):
    end = 0
    while end < len(words) - 1 and words[end].upper() in SURNAME_PARTICLES:
        end += 1
    rest = words[end + 1:]
    return {
        "last_name": (words[:end + 1], GUESS),
        "first_name": (rest[:1], GUESS),
        "middle_name": (rest[1:], GUESS),
    }


def parse_name(raw, name_format=AUTO, ocr_confidence=1.0):
    """
    name_format is what the card's template says to expect. COMMA means the surname comes
    first; if OCR lost the comma the split is still surname-first, but marked as a guess.
    Parts that were not found are left out, never returned as "" or a placeholder.
    """
    text = _normalize(raw)
    tokens, suffix = _take_suffix(text.split())
    body = " ".join(tokens).strip(" ,")

    if "," in body:
        found, split = COMMA, _split_comma(body)
    elif name_format == COMMA:
        found, split = NO_COMMA, _split_surname_first(_words(body))
    else:
        found, split = NO_COMMA, _split_given_first(_words(body))

    parts, confidence = {}, {}
    for name, (words, certainty) in split.items():
        value = _display(words)
        if value and not is_placeholder(value):
            parts[name] = value
            confidence[name] = round(ocr_confidence * certainty, 2)
    if suffix:
        parts["suffix"] = suffix
        confidence["suffix"] = round(ocr_confidence * 0.95, 2)
    return ParsedName(raw=str(raw or ""), format=found, parts=parts, confidence=confidence)


# ---------------------------------------------------------------------------
# Typed values: the same rules whether a value was autofilled or typed
# ---------------------------------------------------------------------------
def clean_name_part(value):
    return _normalize(value)


def clean_middle_name(value):
    """A script that sends the text "null" or "undefined" means "no middle name"."""
    text = _normalize(value)
    return "" if text.lower() in ("null", "undefined") else text


def clean_suffix(value):
    text = _normalize(value)
    return PARSED_SUFFIXES.get(text.rstrip(".").upper(), "V" if text.upper() == "V" else text)


def check_name_part(value, required=True):
    """Return an error message, or None when the value is acceptable."""
    if not value:
        return REQUIRED if required else None
    if is_placeholder(value):
        return "Enter a real name." if required else "Enter a real name, or leave this blank."
    if len(value) > MAX_NAME_LENGTH:
        return f"Use {MAX_NAME_LENGTH} characters or fewer."
    if not NAME_PART_RE.match(value):
        return LETTERS_ONLY
    return None


def check_suffix(value):
    if not value or value in SUFFIXES:
        return None
    if len(value) > MAX_SUFFIX_LENGTH:
        return f"Use {MAX_SUFFIX_LENGTH} characters or fewer."
    if is_placeholder(value) or not SUFFIX_RE.match(value):
        return "Enter a suffix such as Jr, Sr or III, or leave this blank."
    return None
