"""
In-process Stanza tokenizer for Spanish.
Runs locally — no separate API server needed.
Downloads the Spanish model on first use.

Used ONLY for:
  1. Tokenizing usage sentences (finding which token matches the original word)
  2. Getting coarse POS (UPOS) for non-verb consistency checks
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .helpers import text_eq_loose, text_eq_strict

# Lazy-load Stanza so import doesn't block
_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        import stanza
        stanza.download("es", processors="tokenize,pos,lemma", verbose=False)
        _nlp = stanza.Pipeline(
            lang="es",
            processors="tokenize,pos,lemma",
            tokenize_no_ssplit=True,
            use_gpu=False,
            verbose=False,
        )
    return _nlp


def tokenize(text: str) -> List[Dict[str, Any]]:
    """
    Tokenize a Spanish sentence.  Returns list of token dicts:
        {text, lemma, upos, xpos, feats}
    """
    if not text or not text.strip():
        return []

    nlp = _get_nlp()
    doc = nlp(text)

    tokens = []
    for sent in doc.sentences:
        for w in sent.words:
            tokens.append({
                "text": w.text,
                "lemma": w.lemma,
                "upos": w.upos or "",
                "xpos": w.xpos or "",
                "feats": w.feats or "",
            })
    return tokens


def find_token(
    tokens: List[Dict[str, Any]], word: str,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """
    Find the token matching ``word``.

    Returns (token_or_None, used_loose_match).
    Tries strict (accent-preserving) first, then loose (accent-stripped) fallback.
    """
    if not tokens or not word:
        return None, False

    for t in tokens:
        if text_eq_strict(t.get("text", ""), word):
            return t, False

    for t in tokens:
        if text_eq_loose(t.get("text", ""), word):
            return t, True

    return None, False


def tokenize_with_retry(
    usage: str, original_word: str,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], bool, bool]:
    """
    Tokenize and find the matching token, with a PROPN retry.

    If the first match is PROPN (likely sentence-initial capitalization),
    retries with the first occurrence lowercased.

    Returns (tokens, matched_token, used_loose, used_retry).
    """
    tokens = tokenize(usage)
    match, loose = find_token(tokens, original_word)

    retry_used = False
    if match and (match.get("upos") or "").upper() == "PROPN":
        if usage.startswith(original_word):
            lowered_usage = original_word[:1].lower() + original_word[1:] + usage[len(original_word):]
            lowered_word = original_word[:1].lower() + original_word[1:]

            tokens2 = tokenize(lowered_usage)
            m2, l2 = find_token(tokens2, original_word)
            if not m2:
                m2, l2 = find_token(tokens2, lowered_word)

            if m2 and (m2.get("upos") or "").upper() != "PROPN":
                tokens, match, loose, retry_used = tokens2, m2, l2, True

    return tokens, match, loose, retry_used