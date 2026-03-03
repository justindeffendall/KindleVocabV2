"""
Merriam-Webster Spanish Dictionary API client.
Results are cached to disk (JSON file) so re-runs don't re-fetch.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from .config import MW_API_KEY, MW_BASE_URL, MW_MAX_RETRIES, MW_SLEEP_SEC, MW_TIMEOUT_SEC
from .helpers import nfc
from .logger import ForensicLogger

# ── Label normalization ──────────────────────────────────────────────────────

_LABEL_MAP = [
    ("participle", "participle"), ("verb", "verb"), ("noun", "noun"),
    ("adjective", "adjective"), ("adverb", "adverb"), ("pronoun", "pronoun"),
    ("preposition", "preposition"), ("conjunction", "conjunction"),
    ("interjection", "interjection"), ("article", "article"),
    ("determiner", "determiner"), ("numeral", "numeral"),
]


def _normalize_label(fl: str) -> str:
    if not isinstance(fl, str):
        return ""
    s = fl.strip().lower()
    for keyword, label in _LABEL_MAP:
        if keyword in s:
            return label
    return fl.strip() if s else ""


def _strip_homograph(meta_id: str) -> str:
    return meta_id.split(":", 1)[0] if isinstance(meta_id, str) else ""


def _headword(entry: dict) -> str:
    meta = entry.get("meta")
    if not isinstance(meta, dict):
        return ""
    mid = meta.get("id")
    return _strip_homograph(mid) if isinstance(mid, str) else ""


def _match_key(s: str) -> str:
    return nfc(s).casefold()


# ── HTTP with retries ────────────────────────────────────────────────────────

def _get_json(url: str, session: requests.Session) -> Any:
    last_err: Optional[Exception] = None
    for attempt in range(1, MW_MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=MW_TIMEOUT_SEC)
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(2 ** attempt, 20))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_err = e
            time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"MW failed after {MW_MAX_RETRIES} retries: {last_err}")


# ── Single stem lookup ───────────────────────────────────────────────────────

def _lookup_one(stem: str, session: requests.Session) -> Dict[str, Any]:
    """Call MW for one stem.  Returns {shortdefs, label, exact_match, error}."""
    result: Dict[str, Any] = {
        "shortdefs": [], "label": "", "exact_match": False, "error": None,
    }
    url = f"{MW_BASE_URL}/{requests.utils.quote(stem)}?key={MW_API_KEY}"
    try:
        payload = _get_json(url, session)
    except Exception as e:
        result["error"] = str(e)
        return result

    if not isinstance(payload, list) or not payload:
        return result
    if all(isinstance(x, str) for x in payload):
        return result  # suggestions, no definitions

    stem_key = _match_key(stem)
    if not stem_key:
        return result

    matched = [
        e for e in payload
        if isinstance(e, dict) and _match_key(_headword(e)) == stem_key
    ]
    if not matched:
        return result

    result["exact_match"] = True
    seen: Set[str] = set()
    for entry in matched:
        if not result["label"]:
            fl = entry.get("fl")
            if isinstance(fl, str) and fl.strip():
                result["label"] = _normalize_label(fl)
        for d in entry.get("shortdef") or []:
            if isinstance(d, str):
                d = d.strip()
                if d and d not in seen:
                    seen.add(d)
                    result["shortdefs"].append(d)
    return result


# ── Batch lookup with disk cache ─────────────────────────────────────────────

class MWClient:
    """
    Merriam-Webster client with a JSON disk cache.
    Ensures identical stems are never fetched twice, even across runs.
    """

    def __init__(self, cache_path: Path):
        self._cache_path = cache_path
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._session = requests.Session()
        self._load_cache()

    def _load_cache(self) -> None:
        if self._cache_path.exists():
            try:
                with open(self._cache_path, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        with open(self._cache_path, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False, indent=1)

    def lookup(self, stem: str) -> Dict[str, Any]:
        key = _match_key(stem)
        if key in self._cache:
            return self._cache[key]

        time.sleep(MW_SLEEP_SEC)
        result = _lookup_one(stem, self._session)
        self._cache[key] = result
        self._save_cache()
        return result

    def batch_lookup(
        self, stems: List[str], flog: ForensicLogger,
    ) -> Dict[str, Dict[str, Any]]:
        """Look up all unique stems, returning {stem: result}."""
        unique = list(dict.fromkeys(stems))
        flog.section(f"MW Lookup ({len(unique)} unique stems)")

        results: Dict[str, Dict[str, Any]] = {}
        cached = 0
        fetched = 0

        for i, stem in enumerate(unique, 1):
            key = _match_key(stem)
            if key in self._cache:
                results[stem] = self._cache[key]
                cached += 1
            else:
                res = self.lookup(stem)
                results[stem] = res
                fetched += 1
                status = "ERROR" if res["error"] else (
                    "no match" if not res["exact_match"]
                    else f"{len(res['shortdefs'])} defs"
                )
                flog.bullet(f"[{i}/{len(unique)}] {stem!r} — {status}")

        flog.kv("cached", cached)
        flog.kv("fetched", fetched)
        return results