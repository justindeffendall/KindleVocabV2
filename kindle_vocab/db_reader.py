"""
Extract Spanish lookups from Kindle's vocab.db (SQLite).
Returns one record per LOOKUP — every Anki card gets its own context sentence.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple


def is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16).startswith(b"SQLite format 3")
    except Exception:
        return False


def fetch_lookups(db_path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Returns (records, db_meta).

    Each record:
        stem, original_word, usage, book, authors
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    meta: Dict[str, Any] = {"db_path": str(db_path)}
    for label, sql in [
        ("words_count", "SELECT COUNT(*) FROM WORDS"),
        ("lookups_count", "SELECT COUNT(*) FROM LOOKUPS"),
        ("books_count", "SELECT COUNT(*) FROM BOOK_INFO"),
    ]:
        try:
            meta[label] = cur.execute(sql).fetchone()[0]
        except Exception:
            meta[label] = "?"

    rows = cur.execute(
        """
        SELECT
            W.word      AS original_word,
            W.stem      AS stem,
            L.usage     AS usage,
            L.timestamp AS ts,
            B.title     AS book,
            B.authors   AS authors
        FROM LOOKUPS L
        JOIN WORDS W ON W.id = L.word_key
        LEFT JOIN BOOK_INFO B ON B.id = L.book_key
        WHERE W.lang = 'es'
          AND W.stem IS NOT NULL
          AND TRIM(W.stem) <> ''
        ORDER BY L.timestamp ASC
        """
    ).fetchall()
    conn.close()

    records = []
    for r in rows:
        stem = (r["stem"] or "").strip()
        if not stem:
            continue
        records.append({
            "stem": stem,
            "original_word": (r["original_word"] or "").strip(),
            "usage": r["usage"] or "",
            "book": r["book"] or "",
            "authors": r["authors"] or "",
        })

    meta["total_lookups"] = len(records)
    meta["unique_stems"] = len({r["stem"] for r in records})
    return records, meta