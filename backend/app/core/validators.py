"""Deterministic format validators for extracted profile fields.

These ground per-field confidence in an objective check rather than the vision-LLM's
self-reported certainty (PRD §10: never trust LLM self-report alone as the sole signal).
Used by services/extraction.py's grounding step.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

# Verhoeff checksum tables — the algorithm UIDAI uses for the Aadhaar check digit.
_D_TABLE = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P_TABLE = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def _verhoeff_checksum_valid(number: str) -> bool:
    c = 0
    for i, digit in enumerate(reversed(number)):
        c = _D_TABLE[c][_P_TABLE[i % 8][int(digit)]]
    return c == 0


# --- PAN ---------------------------------------------------------------------


def normalize_pan(value: str) -> str:
    return value.strip().upper().replace(" ", "")


def is_valid_pan(value: str) -> bool:
    return bool(_PAN_RE.match(normalize_pan(value)))


# --- Aadhaar -------------------------------------------------------------------


def normalize_aadhaar(value: str) -> str:
    return re.sub(r"\D", "", value)


def is_valid_aadhaar(value: str) -> bool:
    digits = normalize_aadhaar(value)
    if len(digits) != 12:
        return False
    return _verhoeff_checksum_valid(digits)


# --- DOB -----------------------------------------------------------------------

_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y"]


def parse_dob(value: str) -> date | None:
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt).date()
        except ValueError:
            continue
        if 1900 <= parsed.year <= date.today().year:
            return parsed
    return None


def normalize_dob(value: str) -> str | None:
    parsed = parse_dob(value)
    return parsed.isoformat() if parsed else None


# --- Gender ----------------------------------------------------------------------

_GENDER_MAP = {
    "M": "Male",
    "MALE": "Male",
    "पुरुष": "Male",
    "F": "Female",
    "FEMALE": "Female",
    "महिला": "Female",
    "स्त्री": "Female",
    "O": "Other",
    "OTHER": "Other",
    "TRANSGENDER": "Other",
    "अन्य": "Other",
}


def normalize_gender(value: str) -> str | None:
    return _GENDER_MAP.get(value.strip().upper())


# --- Snippet grounding -----------------------------------------------------------


def _norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip().casefold()


def snippet_contains(value: str, snippet: str) -> bool:
    """Whether `value` genuinely appears in the snippet the model claims it read it
    from. Case-insensitive and whitespace-normalized so formatting noise (extra spaces,
    Aadhaar digit grouping) doesn't fail a real match."""
    if not value or not snippet:
        return False
    value_norm = _norm_text(value)
    snippet_norm = _norm_text(snippet)
    if value_norm in snippet_norm:
        return True
    # Also compare with all whitespace stripped, so "1234 5678 9012" matches
    # a snippet written as "123456789012" or vice versa.
    return re.sub(r"\s+", "", value_norm) in re.sub(r"\s+", "", snippet_norm)
