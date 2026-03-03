"""
Entry point — run this file to start the app.

    python run.py
"""

from kindle_vocab.app import app
from kindle_vocab.config import MW_API_KEY

if __name__ == "__main__":
    if not MW_API_KEY:
        print("⚠  MW_API_KEY not set. Add it to your .env file:")
        print("   MW_API_KEY=your-key-here")
    else:
        print("✓ MW_API_KEY loaded")

    try:
        from kindle_vocab.conjugation import is_available
        if is_available():
            print("✓ verbecc loaded")
        else:
            print("⚠  verbecc not available (pip install verbecc)")
    except Exception as e:
        print(f"⚠  verbecc error: {e}")

    print("✓ Starting Flask on http://127.0.0.1:5000")
    app.run(debug=True)