#!/usr/bin/env python3
"""
Translate translation fields for en-XX pairs using Google Translate API.

- translation_short: translates the word_id itself (en-eo, en-hi, en-sw, en-ru)
- translation_long: translates the English description into the home language (en-eo, en-hi, en-sw)

Run this locally (requires internet access and GOOGLE_API_KEY in .env).
"""

import os
import sqlite3
import time
import requests

# Load .env
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_path):
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))

API_KEY = os.environ.get("GOOGLE_API_KEY")
if not API_KEY:
    raise SystemExit("GOOGLE_API_KEY not set")

# Pairs for translation_short (word_id translated into home lang)
SHORT_PAIRS = [
    #("en", "ru", "Russian"),
]

# Pairs for translation_long (English description translated into home lang)
LONG_PAIRS = [
    ("en", "eo", "Esperanto"),
    ("en", "hi", "Hindi"),
    ("en", "sw", "Swahili"),
]


def translate(text: str, target_lang: str) -> str:
    if not text:
        return text
    response = requests.get(
        "https://translation.googleapis.com/language/translate/v2",
        params={"key": API_KEY, "q": text, "source": "en", "target": target_lang},
        timeout=10,
    )
    data = response.json()
    if "data" in data and "translations" in data["data"]:
        return data["data"]["translations"][0]["translatedText"]
    error = data.get("error", {}).get("message", "unknown error")
    raise RuntimeError(f"Translation API error: {error}")


def main():
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "morpheme_dicts.db"))
    conn.row_factory = sqlite3.Row

    # Translate translation_short = word_id translated into home lang
    for target_lang, home_lang, lang_name in SHORT_PAIRS:
        rows = conn.execute(
            "SELECT word_id, translation_short FROM entries WHERE target_lang=? AND home_lang=? ORDER BY rowid",
            (target_lang, home_lang),
        ).fetchall()

        print(f"\n--- {target_lang}-{home_lang} / {lang_name} — translation_short ({len(rows)} entries) ---")

        for i, row in enumerate(rows):
            word_id = row["word_id"]
            current = row["translation_short"] or ""

            if any(ord(c) > 127 for c in current):
                print(f"  [{i+1}/{len(rows)}] {word_id} (already done, skipping)")
                continue

            new_short = translate(word_id, home_lang)
            conn.execute(
                "UPDATE entries SET translation_short=? WHERE target_lang=? AND home_lang=? AND word_id=?",
                (new_short, target_lang, home_lang, word_id),
            )
            print(f"  [{i+1}/{len(rows)}] {word_id} -> {new_short!r}")
            time.sleep(0.05)

        conn.commit()
        print(f"  Done.")

    # Translate translation_long = English description translated into home lang
    for target_lang, home_lang, lang_name in LONG_PAIRS:
        rows = conn.execute(
            "SELECT word_id, translation_long FROM entries WHERE target_lang=? AND home_lang=? ORDER BY rowid",
            (target_lang, home_lang),
        ).fetchall()

        print(f"\n--- {target_lang}-{home_lang} / {lang_name} — translation_long ({len(rows)} entries) ---")

        for i, row in enumerate(rows):
            word_id = row["word_id"]
            long_ = row["translation_long"] or ""

            if not long_:
                continue

            # Resume: skip if already non-ASCII (works for hi; eo/sw re-translate)
            if any(ord(c) > 127 for c in long_):
                print(f"  [{i+1}/{len(rows)}] {word_id} (already done, skipping)")
                continue

            new_long = translate(long_, home_lang)
            conn.execute(
                "UPDATE entries SET translation_long=? WHERE target_lang=? AND home_lang=? AND word_id=?",
                (new_long, target_lang, home_lang, word_id),
            )
            print(f"  [{i+1}/{len(rows)}] {word_id}: {long_!r} -> {new_long!r}")
            time.sleep(0.05)

        conn.commit()
        print(f"  Done.")

    conn.close()
    print("\nAll pairs updated.")


if __name__ == "__main__":
    main()
