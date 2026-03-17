#!/usr/bin/env python3
"""
Generate xx-yy dict pairs from existing xx-en data.

Two-step process:
  Step 1 (this script, Google Translate, run locally):
    - translation_short: translates word_id into home_lang
    - example_translation: translates the target-lang example sentence into home_lang
    - parts[].home_lang_text: left EMPTY for Claude to fill in (step 2)
    - translation_long: left empty

  Step 2 (regloss_cross_pairs.py, Claude API):
    - Fills in parts[].home_lang_text (morpheme glosses) for all entries
      where home_lang_text is empty

Run locally (requires internet + GOOGLE_API_KEY in .env).
"""

import os
import sqlite3
import time
import requests
from collections import defaultdict

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

# Translation cache: (text, target_lang) -> translated text
_cache: dict = {}


def translate(text: str, target_lang: str, source_lang: str = "en") -> str:
    if not text:
        return text
    key = (text, target_lang, source_lang)
    if key in _cache:
        return _cache[key]
    response = requests.get(
        "https://translation.googleapis.com/language/translate/v2",
        params={"key": API_KEY, "q": text, "source": source_lang, "target": target_lang},
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


def fill_missing_translations(conn, target_lang: str, home_lang: str, entry_row):
    """Update translation_short and example_translation if they are empty."""
    word_id = entry_row["word_id"]
    existing = conn.execute(
        "SELECT translation_short, example_translation FROM entries WHERE target_lang=? AND home_lang=? AND word_id=?",
        (target_lang, home_lang, word_id),
    ).fetchone()
    if not existing:
        return
    updated = False
    new_short = existing["translation_short"]
    new_ex = existing["example_translation"]
    if not new_short:
        new_short = translate(word_id, home_lang, source_lang=target_lang)
        updated = True
    if not new_ex:
        new_ex = translate(entry_row["example_sentence"], home_lang, source_lang=target_lang)
        updated = True
    if updated:
        conn.execute(
            "UPDATE entries SET translation_short=?, example_translation=?, updated_at=datetime('now') WHERE target_lang=? AND home_lang=? AND word_id=?",
            (new_short, new_ex, target_lang, home_lang, word_id),
        )


def insert_cross_entry(conn, target_lang: str, home_lang: str, entry_row, parts_rows):
    """Insert one cross-pair entry into the DB.

    Google Translate handles:
      - translation_short: word_id translated into home_lang
      - example_translation: target-lang example sentence translated into home_lang

    Left empty for Claude (regloss_cross_pairs.py step):
      - parts[].home_lang_text
      - translation_long
    """
    word_id = entry_row["word_id"]

    # Translate word_id from target language into home language (e.g. "Freiheit" -> "свобода")
    new_short = translate(word_id, home_lang, source_lang=target_lang)
    # Translate the example sentence from target language into home language
    new_ex_trans = translate(entry_row["example_sentence"], home_lang, source_lang=target_lang)

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
            "",           # translation_long: filled by Claude later
            entry_row["example_sentence"],
            new_ex_trans,
            entry_row["flag"],
            entry_row["part_count"],
            "generate_cross_pairs.py",
        ),
    )

    for p in parts_rows:
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
                "",    # home_lang_text: filled by Claude later
                None,  # home_lang_details: filled by Claude later
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
    parser.add_argument("--fill-missing", action="store_true",
                        help="Update existing entries where translation_short or example_translation is empty (skips pairs that don't exist yet)")
    args = parser.parse_args()

    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "morpheme_dicts.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")  # speed up bulk inserts

    langs = get_non_english_langs(conn)
    if args.langs:
        langs = [l for l in langs if l in args.langs]
    print(f"Languages: {', '.join(langs)}")

    if args.fill_missing:
        # Update existing entries that have empty translation_short or example_translation.
        # Covers both xx-yy cross pairs and xx-en direct pairs.
        pairs_to_fill = []
        home_langs = langs + ["en"]
        for xx in langs:
            for yy in home_langs:
                if xx == yy:
                    continue
                if pair_exists(conn, xx, yy):
                    pairs_to_fill.append((xx, yy))

        print(f"\n{len(pairs_to_fill)} existing pairs to fill missing translations\n")
        for xx, yy in pairs_to_fill:
            source_entries = get_source_entries(conn, xx, all_entries=True)
            needs_fill = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE target_lang=? AND home_lang=? AND (translation_short='' OR example_translation='')",
                (xx, yy),
            ).fetchone()[0]
            if not needs_fill:
                print(f"  {xx}-{yy}: nothing to fill, skipping")
                continue
            print(f"--- {xx}-{yy} ({needs_fill} entries need filling) ---")
            filled = 0
            for i, (entry_row, _) in enumerate(source_entries):
                fill_missing_translations(conn, xx, yy, entry_row)
                filled += 1
                if filled % 10 == 0:
                    conn.commit()
                    print(f"  {filled}...")
            conn.commit()
            print(f"  Done. Cache size: {len(_cache)} unique translations")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()
        print("\nAll missing translations filled.")
        return

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
