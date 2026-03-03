"""
Process a single lookup record into a validated CSV row.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from . import conjugation, tokenizer
from .config import HIGHLIGHT_STYLE
from .helpers import highlight_word
from .logger import ForensicLogger

# ── POS consistency ──────────────────────────────────────────────────────────

_MW_TO_UPOS: Dict[str, Set[str]] = {
    "verb": {"VERB", "AUX"},
    "participle": {"ADJ", "VERB", "AUX"},
    "noun": {"NOUN", "PROPN"},
    "adjective": {"ADJ"},
    "adverb": {"ADV"},
    "pronoun": {"PRON"},
    "preposition": {"ADP"},
    "conjunction": {"CCONJ", "SCONJ"},
    "article": {"DET"},
    "determiner": {"DET"},
    "numeral": {"NUM"},
    "interjection": {"INTJ"},
}


def _pos_ok(mw_label: str, upos: str, feats: str) -> Tuple[bool, str]:
    lbl = (mw_label or "").lower()
    up = (upos or "").upper()
    ft = feats or ""

    allowed = _MW_TO_UPOS.get(lbl)
    if allowed is None:
        return False, f"POS_UNKNOWN({lbl!r})"

    if lbl == "verb":
        if up in {"VERB", "AUX"} or "VerbForm=" in ft:
            return True, "POS_OK"
        return False, f"POS_MISMATCH(MW=verb,upos={up})"

    if lbl == "participle":
        if "VerbForm=Part" in ft:
            return True, "POS_OK"
        return False, f"POS_MISMATCH(MW=participle,no_VerbForm=Part)"

    if up in allowed:
        return True, "POS_OK"
    return False, f"POS_MISMATCH(MW={lbl},upos={up})"


def _is_verb_label(label: str) -> bool:
    return "verb" in (label or "").lower() or "participle" in (label or "").lower()


# ── Main processor ───────────────────────────────────────────────────────────

def process_record(
    rec: Dict[str, Any],
    mw_result: Dict[str, Any],
    flog: ForensicLogger,
    index: int,
    total: int,
) -> Dict[str, Any]:
    """
    Process one lookup record.  Returns a dict with all CSV columns + 'is_complete'.
    """
    stem = rec["stem"]
    original = rec["original_word"]
    usage = rec["usage"]

    flog.section(f"RECORD {index}/{total}")
    flog.kv("stem", repr(stem))
    flog.kv("original_word", repr(original))
    flog.kv("book", repr(rec["book"]))

    # ── MW ──
    shortdefs = mw_result.get("shortdefs", [])
    label = mw_result.get("label", "")
    mw_exact = mw_result.get("exact_match", False)
    definition = " | ".join(shortdefs)

    flog.sub("MW")
    flog.kv("label", repr(label))
    flog.kv("exact_match", mw_exact)
    flog.kv("defs", len(shortdefs))

    # ── Stanza tokenization ──
    flog.sub("Tokenization")
    tokens = []
    match = None
    used_loose = False
    used_retry = False

    if usage and original:
        tokens, match, used_loose, used_retry = tokenizer.tokenize_with_retry(
            usage, original
        )
        flog.bullet(f"Tokens: {len(tokens)}")
        if used_retry:
            flog.bullet("PROPN retry succeeded")
        if used_loose:
            flog.bullet("WARNING: accent-loose fallback")

    found = match is not None
    s_upos = (match.get("upos") or "").strip() if match else ""
    s_feats = (match.get("feats") or "").strip() if match else ""

    if match:
        flog.kv("match", f"text={match['text']!r} upos={s_upos!r}")
    elif usage and original:
        sample = [t["text"] for t in tokens[:15]]
        flog.bullet(f"No match for {original!r} in {sample!r}")

    # ── Conjugation lookup (verbs/participles only) ──
    flog.sub("Conjugation")
    morphology = ""
    morph_source = "n/a"

    is_verb = _is_verb_label(label) and conjugation.looks_like_infinitive(stem)

    if is_verb:
        conj_matches, conj_note = conjugation.lookup(original, stem)
        flog.kv("matches", len(conj_matches))
        flog.kv("note", conj_note)

        if len(conj_matches) == 1:
            morphology = conj_matches[0].to_pretty()
            morph_source = "conjugation_table"
            flog.bullet(f"DETERMINISTIC: {morphology}")

        elif len(conj_matches) > 1:
            resolved, res_note = conjugation.resolve_ambiguity(conj_matches, s_feats)
            flog.kv("resolution", res_note)
            if resolved:
                morphology = resolved.to_pretty()
                morph_source = f"conjugation_table+stanza({res_note})"
                flog.bullet(f"RESOLVED: {morphology}")
            else:
                morph_source = "ambiguous"
                flog.bullet("UNRESOLVED → incomplete")
        else:
            morph_source = conj_note
            flog.bullet(f"NOT FOUND ({conj_note})")
    else:
        flog.bullet(f"SKIP: {'not verb/participle' if not _is_verb_label(label) else 'stem not infinitive'}")

    # ── Highlight ──
    usage_out = highlight_word(usage, original, HIGHLIGHT_STYLE)

    # ── Validation ──
    flog.sub("Validation")
    reasons: List[str] = []

    if not definition:
        reasons.append("DEF_MISSING")
    if not mw_exact:
        reasons.append("MW_NO_EXACT_MATCH")
    if not found:
        reasons.append("STANZA_TOKEN_NOT_FOUND")
    if used_loose:
        reasons.append("ACCENT_LOOSE_MATCH")

    if found and label:
        ok, msg = _pos_ok(label, s_upos, s_feats)
        if not ok:
            reasons.append(msg)
    elif not found or not label:
        reasons.append("POS_SKIP")

    if is_verb and not morphology:
        reasons.append(f"MORPH_MISSING({morph_source})")

    complete = not reasons
    flog.kv("decision", "COMPLETE" if complete else "INCOMPLETE")
    if reasons:
        flog.bullets([f"FAIL: {r}" for r in reasons])

    return {
        "word": stem,
        "definition": definition or "MISSING",
        "label": label,
        "book": rec["book"],
        "authors": rec["authors"],
        "usage": usage_out,
        "morphology": morphology,
        "morphology_source": morph_source,
        "status": "COMPLETE" if complete else "INCOMPLETE",
        "fail_reasons": " ; ".join(_humanize(r) for r in reasons),
        "is_complete": complete,
    }


def _humanize(code: str) -> str:
    """Convert an internal fail code to a short human-readable explanation."""
    if code == "DEF_MISSING":
        return "No definition found"
    if code == "MW_NO_EXACT_MATCH":
        return "Word not in dictionary"
    if code == "POS_SKIP":
        return "Part of speech could not be verified"
    if code == "STANZA_TOKEN_NOT_FOUND":
        return "Word not found in sentence"
    if code == "ACCENT_LOOSE_MATCH":
        return "Accent mismatch between word and sentence"

    import re

    m = re.match(r"POS_MISMATCH\(MW=(\w+),upos=(\w+)\)", code)
    if m:
        mw = m.group(1)
        upos = m.group(2)
        upos_names = {
            "NOUN": "noun", "VERB": "verb", "ADJ": "adjective",
            "ADV": "adverb", "DET": "determiner", "PRON": "pronoun",
            "ADP": "preposition", "CCONJ": "conjunction", "SCONJ": "conjunction",
            "PROPN": "proper noun", "INTJ": "interjection", "NUM": "numeral",
        }
        ctx = upos_names.get(upos, upos.lower())
        return f"Dictionary says \"{mw}\" but context suggests \"{ctx}\""

    m = re.match(r"MORPH_MISSING\((.+)\)", code)
    if m:
        detail = m.group(1)
        if detail == "form_not_in_table":
            return "Verb form not recognized"
        if detail == "ambiguous":
            return "Multiple possible verb forms"
        if detail == "table_empty":
            return "Verb conjugation unavailable"
        return f"Verb morphology missing ({detail})"

    return code