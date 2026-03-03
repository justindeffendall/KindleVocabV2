# KindleVocabV2

Converts your Kindle's Spanish vocabulary lookups into Anki-ready flashcard CSVs with definitions, usage sentences, and verb morphology.

## What it does

1. Upload your Kindle's `vocab.db` file (found at `/system/vocabulary/vocab.db` on your Kindle)
2. The app extracts every Spanish word you looked up, along with the sentence you were reading
3. Each word gets a definition from the Merriam-Webster Spanish Dictionary API
4. Verb forms get deterministic morphology tags (tense, mood, person, number) via conjugation table lookup
5. You get two CSVs: **complete** (ready for Anki import) and **incomplete** (rows that need review)

## CSV columns

| Column | Description |
|--------|-------------|
| word | Dictionary stem (infinitive for verbs) |
| definition | Pipe-separated definitions from MW |
| label | Part of speech (noun, verb, adjective, etc.) |
| book | Book title from Kindle metadata |
| authors | Author name(s) |
| usage | Original sentence with the looked-up word highlighted in HTML |
| morphology | Human-readable verb form (e.g., "Indicative, Preterite, 3rd person, singular") |
| morphology_source | How the morphology was determined |
| status | COMPLETE or INCOMPLETE |
| fail_reasons | Why a row was routed to incomplete (empty for complete rows) |

## Setup

```bash
# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1   # Windows
# source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Create a .env file with your MW API key
# Get a free key at https://dictionaryapi.com/
echo MW_API_KEY=your-key-here > .env
```

First run downloads the Stanza Spanish NLP model (~200MB) and trains the verbecc conjugation model. Both are cached after that.

## Run

```bash
python -m kindle_vocab.run
```

Open `http://127.0.0.1:5000` and upload your `vocab.db`.

## Architecture

| Module | Role |
|--------|------|
| `config.py` | All settings, reads `.env` |
| `db_reader.py` | SQLite extraction from `vocab.db` |
| `mw_client.py` | Merriam-Webster API with JSON disk cache |
| `conjugation.py` | Deterministic verb conjugation via verbecc |
| `tokenizer.py` | In-process Stanza tokenizer (no separate server) |
| `processor.py` | Single-record validation pipeline |
| `pipeline.py` | Job orchestration |
| `app.py` | Flask routes |
| `logger.py` | Forensic logger for debugging |
| `helpers.py` | Unicode/text utilities |

### Why deterministic conjugation?

Earlier versions used Stanza's neural network for verb morphology tagging. This produced errors like wrong person/number ("coses" tagged as 1st person plural instead of 2nd person singular) and wrong tense ("Hojeé" tagged as present instead of preterite).

V2 generates a complete conjugation table for each verb stem using verbecc, then does a reverse lookup of the surface form. If the form has exactly one match in the table, the morphology is guaranteed correct. Ambiguous forms (e.g., "hablara" = 1st or 3rd person imperfect subjunctive) use Stanza as a tiebreaker, and truly unresolvable cases go to the incomplete CSV rather than guessing wrong.

## Dependencies

- **Flask** — web UI
- **python-dotenv** — `.env` file loading
- **requests** — MW API calls
- **stanza** — tokenization and POS tagging (in-process, no separate server)
- **verbecc** — deterministic Spanish verb conjugation tables
- **tzdata** — timezone data (required by verbecc on Windows)