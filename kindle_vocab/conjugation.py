"""
Deterministic Spanish conjugation engine using verbecc.

For each verb stem (infinitive), generates a COMPLETE conjugation table,
then provides reverse-lookup: surface_form → morphology tags.

verbecc 2.0 returns a CompleteConjugation object whose str() is JSON:
    {"moods": {
        "indicativo": {
            "presente": [
                {"c": ["hablo"], "n": "s", "p": "1", "pr": "yo"},
                {"c": ["hablas"], "n": "s", "p": "2", "pr": "tú"},
                ...
            ],
            ...
        },
        ...
    }}

Each form dict has:
    c:  list of conjugation strings (alternates, usually just one)
    n:  "s" (singular) or "p" (plural)
    p:  "1", "2", or "3"
    pr: pronoun (yo, tú, él, etc.)
    g:  gender, optional ("m" or "f")
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from .helpers import nfc, normalize_key, strip_accents

# ── Lazy-load verbecc ────────────────────────────────────────────────────────

_conjugator = None
_conjugator_available = False


def _get_conjugator():
    global _conjugator, _conjugator_available
    if _conjugator is None:
        try:
            from verbecc import CompleteConjugator
            _conjugator = CompleteConjugator(lang="es")
            _conjugator_available = True
        except Exception as e:
            print(f"⚠ verbecc unavailable: {e}")
            _conjugator_available = False
    return _conjugator


def is_available() -> bool:
    _get_conjugator()
    return _conjugator_available


# ── UD tag mappings ──────────────────────────────────────────────────────────

_MOOD_MAP_RAW = {
    "indicativo": "Ind",
    "subjuntivo": "Sub",
    "imperativo": "Imp",
    "imperativo afirmativo": "Imp",
    "imperativo negativo": "Imp",
    "condicional": "Cnd",
}

_TENSE_MAP_RAW = {
    "presente": "Pres",
    "pretérito imperfecto": "Imp",
    "pretérito perfecto simple": "Past",
    "futuro": "Fut",
    # Subjunctive variants
    "pretérito imperfecto 1": "Imp",
    "pretérito imperfecto 2": "Imp",
    "futuro imperfecto": "Fut",
    # Imperative tenses (verbecc puts mood="imperativo", tense="afirmativo"/"negativo")
    "afirmativo": "Pres",
    "negativo": "Pres",
}

# Pre-normalize for matching
_MOOD_MAP = {normalize_key(k): v for k, v in _MOOD_MAP_RAW.items()}
_TENSE_MAP = {normalize_key(k): v for k, v in _TENSE_MAP_RAW.items()}

# Non-finite mood names (verbecc uses "participo" not "participio")
_NONFINITE = {"infinitivo", "gerundio", "participio", "participo"}

# Number mapping: verbecc "s"/"p" → UD "Sing"/"Plur"
_NUM_MAP = {"s": "Sing", "p": "Plur"}


# ── ConjMatch ────────────────────────────────────────────────────────────────

class ConjMatch:
    """A single conjugation match with UD-style tags."""
    __slots__ = ("mood", "tense", "person", "number", "verbform")

    def __init__(self, mood: str, tense: str, person: str, number: str,
                 verbform: str = "Fin"):
        self.mood = mood
        self.tense = tense
        self.person = person
        self.number = number
        self.verbform = verbform

    def to_feats(self) -> str:
        parts = [f"VerbForm={self.verbform}"]
        if self.mood: parts.append(f"Mood={self.mood}")
        if self.tense: parts.append(f"Tense={self.tense}")
        if self.person: parts.append(f"Person={self.person}")
        if self.number: parts.append(f"Number={self.number}")
        return "|".join(parts)

    def to_pretty(self) -> str:
        _M = {"Ind": "Indicative", "Sub": "Subjunctive",
              "Imp": "Imperative", "Cnd": "Conditional"}
        _T = {"Pres": "Present", "Past": "Preterite",
              "Imp": "Imperfect", "Fut": "Future"}
        _P = {"1": "1st person", "2": "2nd person", "3": "3rd person"}
        _N = {"Sing": "singular", "Plur": "plural"}

        if self.verbform == "Inf": return "Infinitive"
        if self.verbform == "Ger": return "Gerund"
        if self.verbform == "Part":
            return f"Participle ({_N.get(self.number, '')})" if self.number else "Participle"

        parts = []
        if self.mood: parts.append(_M.get(self.mood, self.mood))
        if self.tense: parts.append(_T.get(self.tense, self.tense))
        if self.person: parts.append(_P.get(self.person, self.person))
        if self.number: parts.append(_N.get(self.number, self.number))
        return ", ".join(parts)

    def _key(self) -> tuple:
        return (self.verbform, self.mood, self.tense, self.person, self.number)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ConjMatch) and self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __repr__(self) -> str:
        return f"ConjMatch({self.to_feats()})"


# ── Parsing verbecc output ───────────────────────────────────────────────────

def _parse_conj_result(result: Any) -> dict:
    """
    Convert a verbecc CompleteConjugation object to a plain dict.
    The object's __str__() returns valid JSON.
    """
    if isinstance(result, dict):
        return result
    try:
        return json.loads(str(result))
    except (json.JSONDecodeError, TypeError):
        return {}


def _extract_forms(form_obj: Any) -> List[str]:
    """
    Extract conjugation strings from a verbecc form object.

    form_obj is a dict: {"c": ["hablo"], "n": "s", "p": "1", "pr": "yo"}
    The "c" field is a list of alternate conjugation strings.
    """
    if isinstance(form_obj, dict):
        c = form_obj.get("c", [])
        if isinstance(c, list):
            return [s.strip() for s in c if isinstance(s, str) and s.strip()]
        if isinstance(c, str) and c.strip():
            return [c.strip()]
        return []

    if isinstance(form_obj, str) and form_obj.strip():
        return [form_obj.strip()]

    if isinstance(form_obj, list):
        return [s.strip() for s in form_obj if isinstance(s, str) and s.strip()]

    return []


def _extract_pn(form_obj: Any) -> Tuple[str, str]:
    """
    Extract person and number from a verbecc form object.
    Returns (person, ud_number) e.g. ("1", "Sing").
    """
    if not isinstance(form_obj, dict):
        return ("", "")
    person = str(form_obj.get("p", "")).strip()
    number = _NUM_MAP.get(str(form_obj.get("n", "")).strip(), "")
    return (person, number)


def _is_compound_tense(tense_norm: str) -> bool:
    """
    Check if a tense is compound (uses auxiliary verb).
    Compound tenses like "habría hablado" won't match single-word Kindle lookups.

    Uses an explicit set of known compound tense names rather than substring matching,
    because "perfecto" is a substring of "imperfecto" which is NOT compound.
    """
    compound_names = {
        "perfecto",
        "preterito perfecto",
        "preterito perfecto compuesto",
        "preterito pluscuamperfecto",
        "pluscuamperfecto",
        "preterito anterior",
        "futuro perfecto",
        "futuro compuesto",
        "pasado",
    }
    # "pretérito perfecto simple" is the simple past — NOT compound
    if "simple" in tense_norm:
        return False
    return tense_norm in compound_names


# ── Table builder ────────────────────────────────────────────────────────────

def _build_table(stem: str) -> Dict[str, List[ConjMatch]]:
    """Build reverse lookup: lowercased surface form → list of ConjMatch."""
    table: Dict[str, List[ConjMatch]] = {}
    conj = _get_conjugator()
    if not conj:
        return table

    lookup = stem.strip()
    if lookup.lower().endswith("se") and len(lookup) > 4:
        lookup = lookup[:-2]

    try:
        raw = conj.conjugate(lookup, conjugate_pronouns=False)
    except TypeError:
        try:
            raw = conj.conjugate(lookup)
        except Exception:
            return table
    except Exception:
        return table

    data = _parse_conj_result(raw)
    moods = data.get("moods", {})
    if not isinstance(moods, dict):
        return table

    def add(form: str, match: ConjMatch) -> None:
        key = nfc(form).lower()
        if not key:
            return
        if key not in table:
            table[key] = []
        if match not in table[key]:
            table[key].append(match)

    for mood_raw, tense_dict in moods.items():
        mood_norm = normalize_key(str(mood_raw))

        # Non-finite moods
        is_nonfinite = any(mood_norm.startswith(nf) for nf in _NONFINITE)
        if is_nonfinite:
            if isinstance(tense_dict, dict):
                for _, forms in tense_dict.items():
                    if not isinstance(forms, list):
                        continue
                    for form_obj in forms:
                        for form_str in _extract_forms(form_obj):
                            if "infinitivo" in mood_norm:
                                add(form_str, ConjMatch("", "", "", "", "Inf"))
                            elif "gerundio" in mood_norm:
                                add(form_str, ConjMatch("", "", "", "", "Ger"))
                            elif "particip" in mood_norm:
                                # verbecc uses "participo" not "participio"
                                g = form_obj.get("g", "") if isinstance(form_obj, dict) else ""
                                n = form_obj.get("n", "") if isinstance(form_obj, dict) else ""
                                ud_num = _NUM_MAP.get(n, "Sing")
                                add(form_str, ConjMatch("", "", "", ud_num, "Part"))
            continue

        # Finite moods
        ud_mood = _MOOD_MAP.get(mood_norm, "")
        if not ud_mood:
            for mk, mv in _MOOD_MAP.items():
                if mk in mood_norm or mood_norm in mk:
                    ud_mood = mv
                    break
        if not ud_mood or not isinstance(tense_dict, dict):
            continue

        for tense_raw, forms in tense_dict.items():
            tense_norm = normalize_key(str(tense_raw))

            # Skip compound tenses
            if _is_compound_tense(tense_norm):
                continue

            ud_tense = _TENSE_MAP.get(tense_norm, "")
            if not ud_tense:
                for mk, mv in _TENSE_MAP.items():
                    if mk in tense_norm or tense_norm in mk:
                        ud_tense = mv
                        break
            if not ud_tense or not isinstance(forms, list):
                continue

            for form_obj in forms:
                person, number = _extract_pn(form_obj)
                for form_str in _extract_forms(form_obj):
                    if person and number:
                        add(form_str, ConjMatch(ud_mood, ud_tense, person, number))

    # Ensure infinitive is always in the table
    add(lookup, ConjMatch("", "", "", "", "Inf"))

    # Generate participle gender/number variants
    # verbecc only returns the masculine singular (e.g., "hablado")
    # We derive: hablada, hablados, habladas / vivida, vividos, vividas
    _generate_participle_variants(table, add)

    return table


def _generate_participle_variants(
    table: Dict[str, List[ConjMatch]],
    add,
) -> None:
    """
    Find participle entries in the table and generate feminine/plural variants.
    -ado → -ada (f.sg), -ados (m.pl), -adas (f.pl)
    -ido → -ida (f.sg), -idos (m.pl), -idas (f.pl)
    Also handles irregular: -to → -ta, -tos, -tas / -cho → -cha, -chos, -chas
    """
    # Collect existing participle forms
    participles = []
    for form_key, matches in list(table.items()):
        for m in matches:
            if m.verbform == "Part":
                participles.append(form_key)
                break

    for base in participles:
        variants = []
        if base.endswith("ado"):
            root = base[:-3]
            variants = [
                (root + "ada", "Sing"),
                (root + "ados", "Plur"),
                (root + "adas", "Plur"),
            ]
        elif base.endswith("ido"):
            root = base[:-3]
            variants = [
                (root + "ida", "Sing"),
                (root + "idos", "Plur"),
                (root + "idas", "Plur"),
            ]
        elif base.endswith("to"):
            root = base[:-2]
            variants = [
                (root + "ta", "Sing"),
                (root + "tos", "Plur"),
                (root + "tas", "Plur"),
            ]
        elif base.endswith("cho"):
            root = base[:-3]
            variants = [
                (root + "cha", "Sing"),
                (root + "chos", "Plur"),
                (root + "chas", "Plur"),
            ]
        elif base.endswith("so"):
            root = base[:-2]
            variants = [
                (root + "sa", "Sing"),
                (root + "sos", "Plur"),
                (root + "sas", "Plur"),
            ]

        for form, num in variants:
            add(form, ConjMatch("", "", "", num, "Part"))


# ── Cached lookups ───────────────────────────────────────────────────────────

_cache: Dict[str, Dict[str, List[ConjMatch]]] = {}
_cache_lock = threading.Lock()


def get_table(stem: str) -> Dict[str, List[ConjMatch]]:
    """Get (or build+cache) the conjugation table for a stem."""
    key = nfc(stem).lower()
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    table = _build_table(stem)
    with _cache_lock:
        _cache[key] = table
    return table


def looks_like_infinitive(stem: str) -> bool:
    if not stem:
        return False
    s = stem.strip().lower()
    if s in ("ir", "ser"):
        return True
    if len(s) <= 2:
        return False
    if s.endswith(("ar", "er", "ir")):
        return True
    if s.endswith(("arse", "erse", "irse")) and len(s) > 4:
        return True
    return False


def lookup(surface_form: str, stem: str) -> Tuple[List[ConjMatch], str]:
    """
    Reverse-lookup a surface form.

    Returns (matches, note):
      0 matches → "form_not_in_table"
      1 match   → "deterministic" (guaranteed correct)
      2+ matches → "ambiguous"
    """
    if not looks_like_infinitive(stem):
        return [], "not_infinitive"

    table = get_table(stem)
    if not table:
        return [], "table_empty"

    key = nfc(surface_form).lower()
    matches = list(dict.fromkeys(table.get(key, [])))

    # Try stripping reflexive pronouns: "tambaleándose" → "tambaleando"
    if not matches:
        for suffix in ("se", "me", "te", "nos", "os", "le", "lo", "la", "les", "los", "las"):
            if key.endswith(suffix) and len(key) > len(suffix) + 2:
                stripped_key = key[:-len(suffix)]
                # Handle accent restoration: "tambaleándo" → need to check both
                matches = list(dict.fromkeys(table.get(stripped_key, [])))
                if matches:
                    break
                # Also try with accent stripped
                for tk, tv in table.items():
                    if strip_accents(tk) == strip_accents(stripped_key):
                        matches = list(dict.fromkeys(tv))
                        if matches:
                            break
                if matches:
                    break

    if len(matches) == 1:
        return matches, "deterministic"
    if len(matches) > 1:
        return matches, "ambiguous"

    # Accent-stripped fallback on original key
    stripped = strip_accents(key)
    for tk, tv in table.items():
        if strip_accents(tk) == stripped:
            matches = list(dict.fromkeys(tv))
            note = "deterministic_accent_fallback" if len(matches) == 1 else "ambiguous_accent_fallback"
            return matches, note

    return [], "form_not_in_table"


def resolve_ambiguity(
    matches: List[ConjMatch], stanza_feats: str,
) -> Tuple[Optional[ConjMatch], str]:
    """
    Try to disambiguate using Stanza's morphological features.

    Strategy (in order):
      1. Score each match by how many Stanza features it agrees with
         (Mood, Tense, Person, Number).
      2. If exactly one match has the highest score, pick it.
      3. Otherwise unresolvable → None (routes to incomplete).
    """
    if len(matches) <= 1:
        return (matches[0] if matches else None), "unique"

    if not stanza_feats:
        return None, f"unresolved_no_feats({len(matches)}_matches)"

    # Parse Stanza features into a dict
    sf: Dict[str, str] = {}
    for part in stanza_feats.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            sf[k] = v

    # Map our ConjMatch fields to UD feature names
    def score(m: ConjMatch) -> int:
        s = 0
        if m.mood and sf.get("Mood") == m.mood: s += 1
        if m.tense and sf.get("Tense") == m.tense: s += 1
        if m.person and sf.get("Person") == m.person: s += 1
        if m.number and sf.get("Number") == m.number: s += 1
        return s

    scored = [(score(m), m) for m in matches]
    best_score = max(s for s, _ in scored)

    if best_score == 0:
        return None, f"unresolved_no_overlap({len(matches)}_matches)"

    winners = [m for s, m in scored if s == best_score]

    if len(winners) == 1:
        return winners[0], f"resolved_by_stanza(score={best_score})"

    return None, f"unresolved_tied({len(winners)}_at_score_{best_score})"


def dump_table_sample(stem: str) -> Dict[str, Any]:
    """Return a small sample of the table for diagnostic logging."""
    table = get_table(stem)
    sample = {}
    for i, (k, v) in enumerate(table.items()):
        if i >= 15:
            break
        sample[k] = [m.to_feats() for m in v]
    return {"stem": stem, "table_size": len(table), "sample": sample}