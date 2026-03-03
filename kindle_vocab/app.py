"""
Flask web UI for Kindle Vocab CSV Builder.
"""

from __future__ import annotations

import json
import threading
import uuid

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from .config import FLASK_SECRET, OUTPUT_DIR, UPLOAD_DIR
from .db_reader import is_sqlite, scan_books
from .pipeline import get_progress, init_progress, run_job

app = Flask(__name__)
app.secret_key = FLASK_SECRET


def _filters_path(job_id: str):
    return UPLOAD_DIR / f"{job_id}_filters.json"


def _save_filters(job_id: str, filters: dict):
    _filters_path(job_id).write_text(json.dumps(filters))


def _load_filters(job_id: str) -> dict:
    p = _filters_path(job_id)
    if p.exists():
        try:
            filters = json.loads(p.read_text())
            p.unlink(missing_ok=True)
            return filters
        except Exception:
            pass
    return {"mode": "all"}


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/help")
def help_page():
    return render_template("help.html")


@app.post("/upload")
def upload():
    f = request.files.get("vocabdb")
    if not f or f.filename == "":
        flash("Please choose a vocab.db file.")
        return redirect(url_for("index"))

    job_id = uuid.uuid4().hex[:10]
    db_path = UPLOAD_DIR / f"{job_id}_vocab.db"
    f.save(db_path)

    if not is_sqlite(db_path):
        db_path.unlink(missing_ok=True)
        flash("That file doesn't look like a SQLite database.")
        return redirect(url_for("index"))

    return redirect(url_for("select", job_id=job_id))


@app.get("/select/<job_id>")
def select(job_id: str):
    db_path = UPLOAD_DIR / f"{job_id}_vocab.db"
    if not db_path.exists():
        flash("Upload not found.")
        return redirect(url_for("index"))

    books = scan_books(db_path)
    return render_template("select.html", job_id=job_id, books=books)


@app.post("/select/<job_id>")
def select_submit(job_id: str):
    db_path = UPLOAD_DIR / f"{job_id}_vocab.db"
    if not db_path.exists():
        flash("Upload not found.")
        return redirect(url_for("index"))

    mode = request.form.get("mode", "all")

    if mode == "all":
        _save_filters(job_id, {"mode": "all"})
    elif mode == "by_book":
        selected = request.form.getlist("books")
        if not selected:
            flash("Please select at least one book.")
            return redirect(url_for("select", job_id=job_id))
        # Expand comma-separated book_ids from consolidated groups
        book_ids = []
        for val in selected:
            book_ids.extend(val.split(","))
        _save_filters(job_id, {"mode": "by_book", "selected": book_ids})
    elif mode == "by_author":
        selected = request.form.getlist("authors")
        if not selected:
            flash("Please select at least one author.")
            return redirect(url_for("select", job_id=job_id))
        _save_filters(job_id, {"mode": "by_author", "selected": selected})
    else:
        _save_filters(job_id, {"mode": "all"})

    return redirect(url_for("process_async", job_id=job_id))


@app.get("/process/<job_id>")
def process_async(job_id: str):
    db_path = UPLOAD_DIR / f"{job_id}_vocab.db"
    if not db_path.exists():
        flash("Upload not found.")
        return redirect(url_for("index"))

    filters = _load_filters(job_id)
    init_progress(job_id)

    def worker():
        try:
            run_job(job_id, filters=filters)
        except Exception as e:
            from .pipeline import update_progress
            update_progress(job_id, state="error", message=str(e))

    threading.Thread(target=worker, daemon=True).start()
    return render_template("loading.html", job_id=job_id)


@app.get("/status/<job_id>")
def status(job_id: str):
    return jsonify(get_progress(job_id) or {"state": "unknown"})


@app.get("/results/<job_id>")
def results(job_id: str):
    p = get_progress(job_id)
    if not p:
        flash("No results found.")
        return redirect(url_for("index"))
    if p.get("state") == "running":
        return render_template("loading.html", job_id=job_id)
    if p.get("state") == "error":
        flash("Processing failed: " + p.get("message", "Unknown error"))
        return redirect(url_for("index"))
    return render_template(
        "result.html",
        job_id=job_id,
        count=p.get("count", 0),
        meta=p.get("meta", {}),
        csv_name=p.get("csv_name", ""),
        incomplete_csv_name=p.get("incomplete_csv_name", ""),
        log_name=p.get("log_name", ""),
    )


@app.get("/download/<filename>")
def download(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        flash("File not found.")
        return redirect(url_for("index"))
    return send_file(path, as_attachment=True)