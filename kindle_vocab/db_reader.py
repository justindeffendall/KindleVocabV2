"""
Extract Spanish lookups from Kindle's vocab.db (SQLite).
Returns one record per LOOKUP -- every Anki card gets its own context sentence.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def is_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16).startswith(b"SQLite format 3")
    except Exception:
        return False


def scan_books(db_path: Path) -> List[Dict[str, Any]]:
    """
    Quick scan of the database to get book/author combos with lookup counts.
    Consolidates entries with identical title+author into one item.
    Returns a list of dicts: {book_ids, title, authors, lookup_count}
    sorted by lookup_count descending.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute(
        """
        SELECT
            B.id        AS book_id,
            B.title     AS title,
            B.authors   AS authors,
            COUNT(L.id) AS lookup_count
        FROM LOOKUPS L
        JOIN WORDS W ON W.id = L.word_key
        LEFT JOIN BOOK_INFO B ON B.id = L.book_key
        WHERE W.lang = 'es'
          AND W.stem IS NOT NULL
          AND TRIM(W.stem) <> ''
        GROUP BY B.id, B.title, B.authors
        ORDER BY lookup_count DESC
        """
    ).fetchall()
    conn.close()

    # Consolidate identical title+author pairs
    groups: dict = {}
    for r in rows:
        title = r["title"] or "(Unknown Book)"
        authors = r["authors"] or "(Unknown Author)"
        key = (title, authors)
        if key not in groups:
            groups[key] = {
                "book_ids": [],
                "title": title,
                "authors": authors,
                "lookup_count": 0,
            }
        groups[key]["book_ids"].append(r["book_id"] or "")
        groups[key]["lookup_count"] += r["lookup_count"]

    return sorted(groups.values(), key=lambda g: g["lookup_count"], reverse=True)


def fetch_lookups(
    db_path: Path,
    filters: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Returns (records, db_meta).

    filters dict:
        {"mode": "all"}
        {"mode": "by_book", "selected": ["book_id_1", ...]}
        {"mode": "by_author", "selected": ["Author Name", ...]}
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

    where_extra = ""
    params: list = []
    mode = (filters or {}).get("mode", "all")
    selected = (filters or {}).get("selected", [])

    if mode == "by_book" and selected:
        placeholders = ",".join("?" for _ in selected)
        where_extra = f" AND B.id IN ({placeholders})"
        params = list(selected)
    elif mode == "by_author" and selected:
        placeholders = ",".join("?" for _ in selected)
        where_extra = f" AND B.authors IN ({placeholders})"
        params = list(selected)

    query = f"""
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
          {where_extra}
        ORDER BY L.timestamp ASC
    """

    rows = cur.execute(query, params).fetchall()
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

    meta["filter_mode"] = mode
    if mode != "all":
        meta["filter_selected"] = selected
    meta["total_lookups"] = len(records)
    meta["unique_stems"] = len({r["stem"] for r in records})
    return records, meta