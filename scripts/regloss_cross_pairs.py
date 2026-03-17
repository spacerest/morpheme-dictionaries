#!/usr/bin/env python3
"""
Step 2 of cross-pair generation: fill in empty homeLang values using Claude.

Reads entries where parts.home_lang_text = '' for a given xx-yy pair,
looks up the corresponding xx-en entry for English gloss context, and
asks Claude to generate appropriate home-language glosses.

Run after generate_cross_pairs.py (which handles translation_short and
example_translation via Google Translate).

Usage:
    python regloss_cross_pairs.py --target-lang de --home-lang ru
    python regloss_cross_pairs.py --all   # all pairs with empty glosses
"""

import argparse
import json
import sys
import time
from pathlib import Path

import os
import anthropic
from cost_tracker import CostTracker

# Load .env (for ANTHROPIC_API_KEY)
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"'))

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 10

LANG_NAMES = {
    "ar": "Arabic", "da": "Danish", "de": "German", "eo": "Esperanto",
    "es": "Spanish", "fi": "Finnish", "fr": "French", "hi": "Hindi",
    "it": "Italian", "ja": "Japanese", "ko": "Korean", "nl": "Dutch",
    "no": "Norwegian", "pl": "Polish", "pt": "Portuguese", "ru": "Russian",
    "sl": "Slovenian", "sv": "Swedish", "sw": "Swahili", "tr": "Turkish",
    "zh": "Chinese", "en": "English",
}


def load_prompt(target_lang: str, home_lang: str) -> str:
    template = (PROMPTS_DIR / "regloss_cross.txt").read_text().strip()
    return (template
            .replace("<TARGETLANG>", LANG_NAMES.get(target_lang, target_lang))
            .replace("<HOMELANG>", LANG_NAMES.get(home_lang, home_lang)))


def get_pairs_with_empty_glosses(conn) -> list:
    rows = conn.execute("""
        SELECT DISTINCT p.target_lang, p.home_lang
        FROM parts p
        WHERE p.home_lang != 'en'
          AND p.home_lang_text = ''
        ORDER BY p.target_lang, p.home_lang
    """).fetchall()
    return [(r[0], r[1]) for r in rows]


def get_entries_needing_regloss(conn, target_lang: str, home_lang: str) -> list:
    """Return entries that have at least one empty home_lang_text part OR missing translation_long."""
    word_ids = conn.execute("""
        SELECT DISTINCT p.word_id FROM parts p
        LEFT JOIN entries e ON e.target_lang=p.target_lang AND e.home_lang=p.home_lang AND e.word_id=p.word_id
        WHERE p.target_lang=? AND p.home_lang=?
          AND (p.home_lang_text='' OR p.home_lang_details IS NULL OR e.translation_long='')
    """, (target_lang, home_lang)).fetchall()
    word_ids = [r[0] for r in word_ids]

    result = []
    for word_id in word_ids:
        # Get the xx-yy parts (to find which are empty)
        parts = conn.execute("""
            SELECT part_index, target_lang_text, home_lang_text
            FROM parts WHERE target_lang=? AND home_lang=? AND word_id=?
            ORDER BY part_index
        """, (target_lang, home_lang, word_id)).fetchall()

        # Get English glosses from xx-en for context
        en_parts = conn.execute("""
            SELECT part_index, target_lang_text, home_lang_text
            FROM parts WHERE target_lang=? AND home_lang='en' AND word_id=?
            ORDER BY part_index
        """, (target_lang, word_id)).fetchall()

        en_glosses = [p["home_lang_text"] for p in en_parts]
        translation_short = conn.execute("""
            SELECT translation_short FROM entries
            WHERE target_lang=? AND home_lang=? AND word_id=?
        """, (target_lang, home_lang, word_id)).fetchone()

        result.append({
            "id": word_id,
            "parts": [{"targetLang": p["target_lang_text"]} for p in parts],
            "englishGlosses": en_glosses,
            "translationShort": translation_short["translation_short"] if translation_short else "",
        })

    return result


def apply_regloss(conn, target_lang: str, home_lang: str, word_id: str, glossed_parts: list, translation_long: str = ""):
    """Write homeLang values back to DB."""
    if translation_long:
        conn.execute("""
            UPDATE entries SET translation_long=?, updated_at=datetime('now')
            WHERE target_lang=? AND home_lang=? AND word_id=?
        """, (translation_long, target_lang, home_lang, word_id))

    existing_parts = conn.execute("""
        SELECT part_index, target_lang_text FROM parts
        WHERE target_lang=? AND home_lang=? AND word_id=?
        ORDER BY part_index
    """, (target_lang, home_lang, word_id)).fetchall()

    for i, (db_part, new_part) in enumerate(zip(existing_parts, glossed_parts)):
        conn.execute("""
            UPDATE parts SET home_lang_text=?, home_lang_details=?
            WHERE target_lang=? AND home_lang=? AND word_id=? AND part_index=?
        """, (
            new_part.get("homeLang", ""),
            new_part.get("homeLangDetails") or None,
            target_lang, home_lang, word_id, db_part["part_index"],
        ))


def regloss_pair(conn, target_lang: str, home_lang: str, client, model: str):
    pair = f"{target_lang}-{home_lang}"
    prompt = load_prompt(target_lang, home_lang)
    entries = get_entries_needing_regloss(conn, target_lang, home_lang)

    if not entries:
        print(f"[{pair}] No empty glosses found, skipping")
        return

    print(f"[{pair}] {len(entries)} entries to regloss")
    batches = [entries[i:i+BATCH_SIZE] for i in range(0, len(entries), BATCH_SIZE)]
    tracker = CostTracker(script="regloss_cross_pairs", pair=pair, model=model)

    for batch_num, batch in enumerate(batches, 1):
        print(f"  Batch {batch_num}/{len(batches)}...", end=" ", flush=True)
        user_msg = "Please provide homeLang glosses for these entries:\n\n" + \
                   json.dumps({"words": batch}, ensure_ascii=False, indent=2)

        for attempt in range(1, 4):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=2048,
                    system=prompt,
                    messages=[{"role": "user", "content": user_msg}],
                )
                tracker.add(response.usage)
                raw = response.content[0].text.strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                result = json.loads(raw).get("words", [])

                for entry, glossed in zip(batch, result):
                    if entry["id"] == glossed.get("id"):
                        apply_regloss(
                            conn, target_lang, home_lang, entry["id"],
                            glossed.get("parts", []),
                            translation_long=glossed.get("translationLong", ""),
                        )

                conn.commit()
                print(f"done")
                break

            except json.JSONDecodeError as e:
                if attempt < 3:
                    print(f"parse error, retrying... ", end="")
                    time.sleep(1)
                else:
                    print(f"FAILED: {e}")
            except anthropic.RateLimitError:
                wait = 20 * attempt
                print(f"rate limit, waiting {wait}s... ", end="")
                time.sleep(wait)

        if batch_num < len(batches):
            time.sleep(0.3)

    tracker.finish()
    print(f"[{pair}] Done.")


def main():
    parser = argparse.ArgumentParser(description="Fill empty homeLang glosses using Claude")
    parser.add_argument("--target-lang", metavar="CODE")
    parser.add_argument("--home-lang", metavar="CODE")
    parser.add_argument("--all", action="store_true", help="Process all pairs with empty glosses")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    if not args.all and not (args.target_lang and args.home_lang):
        parser.error("either --all or both --target-lang and --home-lang are required")

    from morpheme_db import get_db
    conn = get_db(args.db)
    client = anthropic.Anthropic(api_key=args.api_key)

    if args.all:
        pairs = get_pairs_with_empty_glosses(conn)
        print(f"Found {len(pairs)} pairs with empty glosses")
    else:
        pairs = [(args.target_lang, args.home_lang)]

    for target_lang, home_lang in pairs:
        regloss_pair(conn, target_lang, home_lang, client, args.model)

    conn.close()


if __name__ == "__main__":
    main()
