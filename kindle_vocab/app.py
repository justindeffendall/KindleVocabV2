"""
Flask web UI for Kindle Vocab CSV Builder.
"""

from __future__ import annotations

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

from .config import FLASK_SECRET, MW_API_KEY, OUTPUT_DIR, UPLOAD_DIR
from .db_reader import is_sqlite
from .pipeline import get_progress, init_progress, run_job

app = Flask(__name__)
app.secret_key = FLASK_SECRET


@app.get("/")
def index():
    return render_template("index.html")


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

    return redirect(url_for("process_async", job_id=job_id))


@app.get("/process/<job_id>")
def process_async(job_id: str):
    db_path = UPLOAD_DIR / f"{job_id}_vocab.db"
    if not db_path.exists():
        flash("Upload not found.")
        return redirect(url_for("index"))

    init_progress(job_id)

    def worker():
        try:
            run_job(job_id)
        except Exception as e:
            from .pipeline import _progress, _progress_lock
            with _progress_lock:
                _progress[job_id].update({"state": "error", "message": str(e)})

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