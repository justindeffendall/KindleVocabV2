"""
Configuration — single source of truth for all settings.
Reads from .env via python-dotenv.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Flask
FLASK_SECRET = os.getenv("FLASK_SECRET", "dev-secret-change-me")

# Merriam-Webster Spanish Dictionary API
MW_API_KEY = os.getenv("MW_API_KEY", "")
MW_BASE_URL = "https://www.dictionaryapi.com/api/v3/references/spanish/json"
MW_TIMEOUT_SEC = 20
MW_MAX_RETRIES = 5
MW_SLEEP_SEC = 0.25

# Anki highlight
HIGHLIGHT_COLOR = "#c2410c"
HIGHLIGHT_STYLE = f"font-weight:600; color:{HIGHLIGHT_COLOR};"

# CSV columns
CSV_COLUMNS = [
    "word",
    "definition",
    "label",
    "book",
    "authors",
    "usage",
    "morphology",
    "morphology_source",
    "status",
    "fail_reasons",
]