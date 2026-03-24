"""
Job orchestration — the engine room.

Coordinates: DB extraction → MW lookups → conjugation tables → record processing → CSV output.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import conjugation
from .config import CSV_COLUMNS, MW_API_KEY, OUTPUT_DIR, UPLOAD_DIR
from .db_reader import fetch_lookups
from .logger import ForensicLogger
from .mw_client import MWClient
from .processor import process_record

# ── Progress tracking (file-based for multi-worker compatibility) ─────────

def _progress_path(job_id: str) -> Path:
    return OUTPUT_DIR / f"{job_id}_progress.json"


def update_progress(job_id: str, **kw: Any) -> None:
    import json
    p = _progress_path(job_id)
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        data = {}
    data.update(kw)
    p.write_text(json.dumps(data))


def get_progress(job_id: str) -> Dict[str, Any] | None:
    import json
    p = _progress_path(job_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def init_progress(job_id: str) -> None:
    import json
    p = _progress_path(job_id)
    p.write_text(json.dumps({
        "state": "running", "current": 0, "total": 1, "message": "Starting...",
    }))


# ── Cleanup old files ────────────────────────────────────────────────────────

_CLEANUP_AGE_SEC = 30 * 60  # 30 minutes


def _cleanup_old_files(current_job_id: str) -> None:
    """
    Delete upload and output files older than 30 minutes,
    skipping files belonging to the current job and the MW cache.
    """
    now = time.time()
    for directory in (UPLOAD_DIR, OUTPUT_DIR):
        if not directory.exists():
            continue
        for f in directory.iterdir():
            if not f.is_file():
                continue
            # Keep MW cache
            if f.name == "mw_cache.json":
                continue
            # Keep current job's files
            if current_job_id in f.name:
                continue
            # Delete if older than threshold
            try:
                age = now - f.stat().st_mtime
                if age > _CLEANUP_AGE_SEC:
                    f.unlink()
            except Exception:
                pass


# ── Main pipeline ────────────────────────────────────────────────────────────

def run_job(job_id: str, filters: Optional[Dict[str, Any]] = None) -> None:
    """
    Full pipeline:
      1. Extract lookups from DB
      2. MW batch lookup (disk-cached)
      3. Pre-build conjugation tables
      4. Process each record (tokenize + validate)
      5. Write complete / incomplete CSVs + forensic log
    """
    if not MW_API_KEY:
        raise RuntimeError("MW_API_KEY not set. Add it to your .env file.")

    if not conjugation.is_available():
        raise RuntimeError("verbecc not available. Run: pip install verbecc")

    db_path = UPLOAD_DIR / f"{job_id}_vocab.db"
    _cleanup_old_files(job_id)
    records, db_meta = fetch_lookups(db_path, filters=filters)
    total = len(records)

    update_progress(job_id, total=total, current=0, message="Extracting lookups…")

    out_csv = OUTPUT_DIR / f"{job_id}_complete.csv"
    inc_csv = OUTPUT_DIR / f"{job_id}_incomplete.csv"
    log_path = OUTPUT_DIR / f"{job_id}_forensic.txt"
    mw_cache_path = OUTPUT_DIR / "mw_cache.json"

    flog = ForensicLogger(log_path)
    flog.kv("job_id", job_id)
    flog.kv("total_lookups", total)
    flog.kv("unique_stems", db_meta.get("unique_stems", "?"))

    try:
        # ── Step 1: Load Stanza (first run downloads model) ──
        update_progress(job_id, message="Loading model…")
        flog.sub("Stanza Init")
        from .tokenizer import tokenize
        tokenize("hola")  # warm up
        flog.bullet("OK")

        # ── Step 2: MW batch lookup ──
        update_progress(job_id, message="Looking up definitions…")
        mw = MWClient(mw_cache_path)
        stems = [r["stem"] for r in records]
        mw_cache = mw.batch_lookup(
            stems, flog,
            progress_fn=lambda i, n: update_progress(job_id, message=f"Looking up definitions {i}/{n}…"),
        )

        # ── Step 3: Pre-build conjugation tables ──
        update_progress(job_id, message="Building conjugation tables…")
        flog.sub("Conjugation Pre-build")
        unique_stems = list(dict.fromkeys(stems))
        verb_count = 0
        first_dump = True

        for s in unique_stems:
            if conjugation.looks_like_infinitive(s):
                table = conjugation.get_table(s)
                verb_count += 1

                if first_dump:
                    sample = conjugation.dump_table_sample(s)
                    flog.kv("first_verb", s)
                    flog.kv("table_size", sample["table_size"])
                    for k, v in list(sample["sample"].items())[:10]:
                        flog.bullet(f"{k!r} → {v}")
                    first_dump = False

        flog.kv("verb_stems_built", verb_count)

        # ── Step 4: Process each record ──
        ok_count = 0
        bad_count = 0

        with (
            out_csv.open("w", newline="", encoding="utf-8-sig") as f_ok,
            inc_csv.open("w", newline="", encoding="utf-8-sig") as f_bad,
        ):
            w_ok = csv.writer(f_ok)
            w_bad = csv.writer(f_bad)
            w_ok.writerow(CSV_COLUMNS)
            w_bad.writerow(CSV_COLUMNS)

            for i, rec in enumerate(records, 1):
                mw_result = mw_cache.get(rec["stem"], {
                    "shortdefs": [], "label": "", "exact_match": False,
                })

                row_data = process_record(rec, mw_result, flog, i, total)
                row = [row_data[c] for c in CSV_COLUMNS]

                if row_data["is_complete"]:
                    w_ok.writerow(row)
                    ok_count += 1
                else:
                    w_bad.writerow(row)
                    bad_count += 1

                if i % 25 == 0 or i == total:
                    update_progress(
                        job_id, current=i,
                        message=f"Processing {i}/{total}…",
                    )

    finally:
        flog.close()

    # ── Final state ──
    meta = dict(db_meta)
    meta.update({
        "complete_rows": ok_count,
        "incomplete_rows": bad_count,
    })

    update_progress(
        job_id,
        state="done",
        csv_name=out_csv.name,
        incomplete_csv_name=inc_csv.name,
        log_name=log_path.name,
        count=ok_count,
        meta=meta,
        message="Done",
        current=total,
        total=total,
    )