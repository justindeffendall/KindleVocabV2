"""
Text and Unicode helpers used across the project.
"""

import re
import unicodedata


def nfc(s: str) -> str:
    """NFC-normalize and strip whitespace."""
    return unicodedata.normalize("NFC", s.strip()) if isinstance(s, str) else ""


def casefold(s: str) -> str:
    """NFC + casefold (accent-strict, case-insensitive)."""
    return nfc(s).casefold()


def strip_accents(s: str) -> str:
    """Remove combining diacritical marks."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in nfkd if unicodedata.category(ch) != "Mn")


def normalize_key(s: str) -> str:
    """
    Normalize a verbecc mood/tense key for matching.
    Lowercase, strip accents, replace hyphens/underscores with spaces, collapse whitespace.
    """
    s = strip_accents(s.strip().lower())
    s = re.sub(r"[-_]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def text_eq_strict(a: str, b: str) -> bool:
    """Accent-strict, case-insensitive equality."""
    return bool(a and b and casefold(a) == casefold(b))


def text_eq_loose(a: str, b: str) -> bool:
    """Accent-insensitive, case-insensitive equality (fallback)."""
    return bool(a and b and strip_accents(casefold(a)) == strip_accents(casefold(b)))


# Spanish letter class for word-boundary regex
_ES_CHARS = r"A-Za-zÁÉÍÓÚÜÑáéíóúüñ"


def highlight_word(usage: str, word: str, style: str) -> str:
    """
    Word-boundary-aware highlight of first occurrence.
    Falls back to plain replace if boundary match misses.
    """
    if not usage or not word or word not in usage:
        return usage or ""

    pattern = re.compile(
        rf"(?<![{_ES_CHARS}])" + re.escape(word) + rf"(?![{_ES_CHARS}])"
    )
    replacement = f'<span style="{style}">{word}</span>'
    result, n = pattern.subn(replacement, usage, count=1)

    if n == 0:
        return usage.replace(word, replacement, 1)
    return result