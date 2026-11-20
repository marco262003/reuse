"""
Turn OCR lines into registration fields using a school's ID template
(reuse_project/registration_config.py). An untuned template still works: anything that
cannot be found is left out, and the person types it.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from ..names import NO_COMMA, parse_name

LABELLED = 1.0  # the value followed its printed label
PATTERN = 0.8   # the value matched the template's pattern
GUESSED = 0.6   # the value was picked by how it looks

SEPARATORS = r"\s*(?:no\.?|number|#)?\s*[:\-–.]*\s*"
DATE_FORMATS = ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y")
ORDINALS = {"first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5", "sixth": "6"}
PROGRAM_RE = re.compile(r"^(?:bachelor|master|doctor|associate|diploma)\b|^(?:b\.?s\.?|a\.?b\.?|b\.?a\.?|m\.?s\.?|m\.?a\.?)\s+[A-Za-z]", re.I)
YEAR_LEVEL_RE = re.compile(r"\b([1-6])(?:st|nd|rd|th)\s+year\b|\b(first|second|third|fourth|fifth|sixth)\s+year\b", re.I)
VALIDITY_RE = re.compile(r"\bA\.?\s?Y\.?\s*\d{4}\s*[-–]\s*\d{4}\b", re.I)
NAME_LINE_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ .,'\-])*$")  # letters and name punctuation only
COMMA_NAME_RE = re.compile(r"^[^\d,]{2,},\s*[^\d,]{2,}$")


@dataclass
class Extraction:
    fields: dict = field(default_factory=dict)  # name -> {"value": str, "confidence": float}
    raw_name: str = ""
    name_format: str = ""
    name_low_confidence: bool = False

    def put(self, name, value, confidence):
        value = str(value or "").strip()
        if value and name not in self.fields:
            self.fields[name] = {"value": value, "confidence": round(min(1.0, confidence), 2)}


def _labelled(lines, labels):
    """
    Find a line that starts with a label: "LABEL: value", or the label alone with its value on
    the next line. Returns (value, confidence, index of the value's line) or None.
    """
    for label in sorted(labels, key=len, reverse=True):
        pattern = re.compile(rf"^\W*{re.escape(label)}(?![A-Za-z]){SEPARATORS}(.*)$", re.I)
        for i, line in enumerate(lines):
            match = pattern.match(line.text.strip())
            if not match:
                continue
            value = match.group(1).strip()
            if value.lower().startswith("of "):  # "College of Engineering": the label word is part of the value
                return line.text.strip(), line.confidence, i
            if value:
                return value, line.confidence, i
            if i + 1 < len(lines):
                return lines[i + 1].text.strip(), min(line.confidence, lines[i + 1].confidence), i + 1
    return None


def parse_date(text):
    """Return an ISO date for a plausible birth date, or ""."""
    cleaned = re.sub(r"\s+", " ", text.replace(".", "")).strip()
    for fmt in DATE_FORMATS:
        try:
            found = datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
        return found.isoformat() if date(1900, 1, 1) <= found <= date.today() else ""
    return ""


def _year_level(text):
    match = YEAR_LEVEL_RE.search(text)
    if match:
        return match.group(1) or ORDINALS[match.group(2).lower()]
    digit = re.fullmatch(r"\s*([1-6])(?:st|nd|rd|th)?\s*", text, re.I)
    return digit.group(1) if digit else ORDINALS.get(text.strip().lower(), "")


def _sex(text):
    return {"m": "male", "male": "male", "f": "female", "female": "female"}.get(text.strip().lower(), "")


def _find_name(lines, used, template):
    """The line that looks most like a person's name: a comma line first, else the tallest print."""
    ignore = tuple(phrase.lower() for phrase in template.get("ignore", ()))
    labels = tuple(label for group in template["labels"].values() for label in group)
    candidates = []
    for i, line in enumerate(lines):
        text, low = line.text.strip(), line.text.lower()
        if i in used or not NAME_LINE_RE.match(text) or not 2 <= len(text.split()) <= 6:
            continue
        if any(phrase in low for phrase in ignore) or any(re.search(rf"(?<![a-z]){re.escape(label)}(?![a-z])", low) for label in labels):
            continue
        if PROGRAM_RE.match(text) or low.startswith("college of"):
            continue
        candidates.append((i, line))
    with_comma = [(i, line) for i, line in candidates if COMMA_NAME_RE.match(line.text.strip())]
    if with_comma:
        i, line = with_comma[0]
        return line.text.strip(), line.confidence * PATTERN, i
    if not candidates:
        return None
    i, line = max(candidates, key=lambda item: item[1].height)
    return line.text.strip(), line.confidence * GUESSED, i


def map_fields(lines, template):
    lines = [line for line in lines if line.text.strip()]
    out, used = Extraction(), set()
    found = {}
    for key, labels in template["labels"].items():
        hit = _labelled(lines, labels)
        if hit:
            found[key] = hit
            used.add(hit[2])

    # School ID number: the labelled value, narrowed to the template's pattern when it fits.
    pattern = re.compile(template["student_id_pattern"]) if template.get("student_id_pattern") else None
    if "student_id_number" in found:
        value, confidence, _ = found["student_id_number"]
        match = pattern.search(value) if pattern else None
        out.put("student_id_number", match.group(0) if match else value.split()[0], confidence * LABELLED)
    elif pattern:
        for i, line in enumerate(lines):
            match = pattern.search(line.text)
            if match:
                out.put("student_id_number", match.group(0), line.confidence * PATTERN)
                used.add(i)
                break

    # Name: labelled, else the most name-like line. A surname line ending in a comma takes the next line too.
    name = found.get("name") or _find_name(lines, used, template)
    if name:
        raw, confidence, i = name
        if raw.endswith(",") and i + 1 < len(lines):
            raw = f"{raw} {lines[i + 1].text.strip()}"
        parsed = parse_name(raw, template.get("name_format", "auto"), ocr_confidence=confidence)
        out.raw_name, out.name_format, out.name_low_confidence = raw, parsed.format, parsed.low_confidence
        for part, value in parsed.parts.items():
            out.put(part, value, parsed.confidence[part])
    else:
        out.name_format = NO_COMMA

    if "date_of_birth" in found:
        out.put("date_of_birth", parse_date(found["date_of_birth"][0]), found["date_of_birth"][1])
    if "sex" in found:
        out.put("sex", _sex(found["sex"][0]), found["sex"][1])
    if "year_level" in found:
        out.put("year_level", _year_level(found["year_level"][0]), found["year_level"][1])
    for key in ("program", "college", "address", "id_valid_until"):
        if key in found:
            out.put(key, found[key][0], found[key][1] * LABELLED)

    # Values with no label that can be recognised by their look.
    for i, line in enumerate(lines):
        if i in used:
            continue
        text = line.text.strip()
        if PROGRAM_RE.match(text):
            out.put("program", text, line.confidence * GUESSED)
        elif text.lower().startswith("college of"):
            out.put("college", text, line.confidence * GUESSED)
        elif YEAR_LEVEL_RE.search(text):
            out.put("year_level", _year_level(text), line.confidence * GUESSED)
        elif VALIDITY_RE.search(text):
            out.put("id_valid_until", VALIDITY_RE.search(text).group(0), line.confidence * PATTERN)
    return out
