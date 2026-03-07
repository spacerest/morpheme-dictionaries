#!/usr/bin/env python3
"""
Generate xx-yy dict pairs from existing xx-en data using Google Translate.

For each non-English language pair (xx, yy) where we have xx-en entries:
- Fetches xx-en entries from DB
- Translates all English home-language fields into yy
- Inserts xx-yy entries into DB (skips pairs that already have entries)

Caches translations in memory so repeated glosses (e.g. "not", "person",
"(noun)") only hit the API once per target language.

Run locally (requires internet + GOOGLE_API_KEY in .env).
"""

import os
import sqlite3
import time
import requests
from collections import defaultdict

# Load .env
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))

API_KEY = os.environ.get("GOOGLE_API_KEY")
if not API_KEY:
    raise SystemExit("GOOGLE_API_KEY not set")

# Translation cache: (text, target_lang) -> translated text
_cache: dict = {}


def translate(text: str, target_lang: str) -> str:
    if not text:
        return text
    key = (text, target_lang)
    if key in _cache:
        return _cache[key]
    response = requests.get(
        "https://translation.googleapis.com/language/translate/v2",
        params={"key": API_KEY, "q": text, "source": "en", "target": target_lang},
        timeout=10,
    )
    data = response.json()
    if "data" in data and "translations" in data["data"]:
        result = data["data"]["translations"][0]["translatedText"]
        _cache[key] = result
        time.sleep(0.03)
        return result
    error = data.get("error", {}).get("message", "unknown error")
    raise RuntimeError(f"Google Translate error ({target_lang}): {error}")


def get_non_english_langs(conn) -> list:
    """Return all target_langs that have xx-en entries."""
    rows = conn.execute(
        "SELECT DISTINCT target_lang FROM entries WHERE home_lang='en' AND target_lang != 'en' ORDER BY target_lang"
    ).fetchall()
    return [r[0] for r in rows]


def pair_exists(conn, target_lang: str, home_lang: str) -> bool:
    count = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE target_lang=? AND home_lang=?",
        (target_lang, home_lang),
    ).fetchone()[0]
    return count > 0


def get_source_entries(conn, target_lang: str, all_entries: bool = False, to_verify: bool = False) -> list:
    """Fetch xx-en entries as raw DB rows with their parts.

    all_entries=True: all entries regardless of status
    to_verify=True: only to_verify=1 entries
    default: only curated=1 entries
    """
    if all_entries:
        filter_clause = ""
    elif to_verify:
        filter_clause = "AND to_verify=1"
    else:
        filter_clause = "AND curated=1"
    entries = conn.execute(
        f"SELECT * FROM entries WHERE target_lang=? AND home_lang='en' {filter_clause} ORDER BY rowid",
        (target_lang,),
    ).fetchall()
    result = []
    for e in entries:
        parts = conn.execute(
            "SELECT * FROM parts WHERE target_lang=? AND home_lang='en' AND word_id=? ORDER BY part_index",
            (target_lang, e["word_id"]),
        ).fetchall()
        result.append((e, parts))
    return result


def insert_cross_entry(conn, target_lang: str, home_lang: str, entry_row, parts_rows):
    """Translate and insert one entry into the DB."""
    word_id = entry_row["word_id"]

    new_short = translate(entry_row["translation_short"], home_lang)
    new_long = translate(entry_row["translation_long"], home_lang)
    new_ex_trans = translate(entry_row["example_translation"], home_lang)

    conn.execute(
        """INSERT OR IGNORE INTO entries
            (target_lang, home_lang, word_id, article, display_prefix,
             translation_short, translation_long, example_sentence,
             example_translation, flag, part_count, imported_from, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            target_lang,
            home_lang,
            word_id,
            entry_row["article"],
            entry_row["display_prefix"],
            new_short,
            new_long,
            entry_row["example_sentence"],  # stays in target language
            new_ex_trans,
            entry_row["flag"],
            entry_row["part_count"],
            "generate_cross_pairs.py",
        ),
    )

    for p in parts_rows:
        new_home_text = translate(p["home_lang_text"], home_lang)
        new_details = translate(p["home_lang_details"], home_lang) if p["home_lang_details"] else None

        conn.execute(
            """INSERT OR IGNORE INTO parts
                (target_lang, home_lang, word_id, part_index,
                 target_lang_text, home_lang_text, home_lang_details)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                target_lang,
                home_lang,
                word_id,
                p["part_index"],
                p["target_lang_text"],
                new_home_text,
                new_details,
            ),
        )


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate cross-language dict pairs via Google Translate")
    parser.add_argument("--langs", nargs="+", metavar="CODE",
                        help="Limit to specific language codes (e.g. --langs de ru)")
    parser.add_argument("--all", dest="all_entries", action="store_true",
                        help="Use all entries, not just curated=1")
    parser.add_argument("--to-verify", action="store_true",
                        help="Use to_verify=1 entries instead of curated=1")
    args = parser.parse_args()

    conn = sqlite3.connect("morpheme_dicts.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")  # speed up bulk inserts

    langs = get_non_english_langs(conn)
    if args.langs:
        langs = [l for l in langs if l in args.langs]
    print(f"Languages: {', '.join(langs)}")

    pairs_to_generate = []
    for xx in langs:
        for yy in langs:
            if xx == yy:
                continue
            if pair_exists(conn, xx, yy):
                print(f"  {xx}-{yy}: already exists, skipping")
                continue
            pairs_to_generate.append((xx, yy))

    print(f"\n{len(pairs_to_generate)} pairs to generate\n")

    for xx, yy in pairs_to_generate:
        source_entries = get_source_entries(conn, xx, all_entries=args.all_entries, to_verify=args.to_verify)
        if not source_entries:
            print(f"  No entries found, skipping")
            continue
        print(f"--- {xx}-{yy} ({len(source_entries)} entries) ---")

        for i, (entry_row, parts_rows) in enumerate(source_entries):
            insert_cross_entry(conn, xx, yy, entry_row, parts_rows)
            if (i + 1) % 10 == 0:
                conn.commit()
                print(f"  {i+1}/{len(source_entries)}...")

        conn.commit()
        print(f"  Done. Cache size: {len(_cache)} unique translations")

    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()
    print("\nAll cross pairs generated.")


if __name__ == "__main__":
    main()
