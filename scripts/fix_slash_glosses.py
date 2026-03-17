#!/usr/bin/env python3
"""
Resolve slash/comma-separated home_lang_text values in the parts table.

For each part where home_lang_text contains "/" or ", " (e.g. "away/off" or
"make, do"), sends a single Haiku call to pick the best single gloss for that
morpheme in context. The chosen gloss is written to home_lang_text; the
discarded alternatives go to home_lang_alternates. Uncertain choices are
flagged for human review.

Usage:
    python fix_slash_glosses.py --target-lang de --home-lang en
    python fix_slash_glosses.py --target-lang de --home-lang en --word-set first_release_dictionary
    python fix_slash_glosses.py --target-lang de --home-lang en --dry-run
"""

import argparse
import json
import os
import sys
import time

import anthropic

from cost_tracker import CostTracker
from morpheme_db import get_db, insert_flag

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """\
You are editing morpheme dictionary entries for a language-learning puzzle app.
Each morpheme appears as a tile with a short gloss label. Your task: given a morpheme,
its context word, and a list of alternative gloss labels, pick the single best label.

Rules:
- The label must reflect what THIS morpheme contributes on its own in this specific word,
  not the compound's full meaning.
- Prefer the shorter, more concrete option when the difference is small.
- Labels must be 1-2 words maximum.
- Return only valid JSON, no explanation, no markdown.

Output format:
{"chosen": "the label you picked", "alternates": ["other", "options"], "confident": true}

Set "confident": false if the options are genuinely ambiguous in this context."""


def split_alternatives(text: str) -> list[str] | None:
    """Split a slash/comma home_lang_text into alternatives.

    Returns a list of 2+ tokens if the value looks like competing alternatives,
    or None if it should be left alone (single concept, too many words per token, etc).
    """
    # Strip outer parens if the whole value is wrapped: "(away, off)" -> "away, off"
    stripped = text.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        stripped = stripped[1:-1].strip()

    # Split on "/" or ", " (comma followed by space — avoids splitting "action, process noun")
    import re
    parts = re.split(r"\s*/\s*|\s*,\s+", stripped)
    parts = [p.strip().strip("()") for p in parts if p.strip()]

    if len(parts) < 2:
        return None

    # Skip if any token is more than 3 words — likely a description, not an alternative label
    if any(len(p.split()) > 3 for p in parts):
        return None

    return parts


def query_affected_parts(conn, target_lang: str, home_lang: str,
                          word_set: str = None, audited_after: str = None) -> list:
    params = [target_lang, home_lang]
    filters = [
        "p.target_lang=? AND p.home_lang=?",
        "(p.home_lang_text LIKE '%/%' OR p.home_lang_text LIKE '%, %')",
        "(p.part_role IS NULL OR p.part_role = 'semantic')",
    ]
    if word_set:
        filters.append("e.word_set=?")
        params.append(word_set)
    if audited_after:
        filters.append("e.last_audited >= ?")
        params.append(audited_after)

    sql = f"""
        SELECT p.word_id, p.part_index, p.target_lang_text, p.home_lang_text,
               p.home_lang_details, e.translation_short
        FROM parts p
        JOIN entries e USING (target_lang, home_lang, word_id)
        WHERE {' AND '.join(filters)}
        ORDER BY p.word_id, p.part_index
    """
    return conn.execute(sql, params).fetchall()


def call_haiku(client, target_lang: str, home_lang: str, word_id: str,
               target_lang_text: str, translation_short: str,
               options: list[str], home_lang_details: str | None) -> dict | None:
    user_message = json.dumps({
        "word": word_id,
        "translation": translation_short,
        "morpheme": target_lang_text,
        "options": options,
        "home_lang_details": home_lang_details,
        "target_lang": target_lang,
        "home_lang": home_lang,
    }, ensure_ascii=False, indent=2)
    user_message += f'\n\nPick the single best label for the "{target_lang_text}" tile in "{word_id}".'

    for attempt in range(1, 4):
        try:
            response = client.messages.create(
                model=DEFAULT_MODEL,
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = "\n".join(text.split("\n")[1:])
                text = text.rsplit("```", 1)[0].strip()
            return json.loads(text), response.usage
        except json.JSONDecodeError:
            if attempt == 3:
                print(f"  PARSE ERROR on {word_id}[{target_lang_text}] — skipping")
                return None, None
        except anthropic.RateLimitError:
            wait = 20 * attempt
            print(f"  Rate limit, waiting {wait}s...")
            time.sleep(wait)
        except anthropic.APIError as e:
            print(f"  API ERROR: {e}")
            sys.exit(1)
    return None, None


def main():
    parser = argparse.ArgumentParser(
        description="Resolve slash/comma home_lang_text values using Haiku",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--target-lang", required=True, metavar="CODE")
    parser.add_argument("--home-lang", required=True, metavar="CODE")
    parser.add_argument("--word-set", default=None, metavar="NAME")
    parser.add_argument("--audited-after", default=None, metavar="DATE",
                        help="ISO date: only entries audited on or after this date")
    parser.add_argument("--db", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be changed without writing or calling API")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("MORPHEME_SORT_ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        print("ERROR: No API key. Set ANTHROPIC_API_KEY or pass --api-key.")
        sys.exit(1)

    conn = get_db(args.db)
    pair = f"{args.target_lang}-{args.home_lang}"

    rows = query_affected_parts(conn, args.target_lang, args.home_lang,
                                 word_set=args.word_set, audited_after=args.audited_after)

    # Filter to rows with actual alternatives
    affected = []
    for row in rows:
        word_id, part_index, target_lang_text, home_lang_text, home_lang_details, translation_short = row
        options = split_alternatives(home_lang_text)
        if options:
            affected.append((word_id, part_index, target_lang_text, home_lang_text,
                              home_lang_details, translation_short, options))

    print(f"[{pair}] Found {len(affected)} parts with slash/comma glosses")
    if not affected:
        conn.close()
        return

    if args.dry_run:
        for word_id, part_index, target_lang_text, home_lang_text, _, _, options in affected:
            print(f"  {word_id}[{target_lang_text}]: {home_lang_text!r} → {options}")
        conn.close()
        return

    client = anthropic.Anthropic(api_key=api_key)
    tracker = CostTracker(script="fix_slash_glosses", pair=pair, model=args.model)

    fixed = 0
    flagged = 0

    for word_id, part_index, target_lang_text, home_lang_text, home_lang_details, translation_short, options in affected:
        result, usage = call_haiku(
            client, args.target_lang, args.home_lang,
            word_id, target_lang_text, translation_short,
            options, home_lang_details,
        )
        if usage:
            tracker.add(usage)
        if result is None:
            continue

        chosen = result.get("chosen", "").strip()
        alternates = [a for a in result.get("alternates", []) if a != chosen]
        confident = result.get("confident", True)

        if not chosen or chosen not in options:
            print(f"  WARNING: {word_id}[{target_lang_text}] — unexpected choice {chosen!r}, skipping")
            continue

        conn.execute(
            """UPDATE parts SET home_lang_text=?, home_lang_alternates=?
               WHERE target_lang=? AND home_lang=? AND word_id=? AND part_index=?""",
            (chosen, ", ".join(alternates) if alternates else None,
             args.target_lang, args.home_lang, word_id, part_index),
        )

        if not confident:
            insert_flag(conn, args.target_lang, args.home_lang, word_id, {
                        "category": "uncertain_gloss_choice",
                        "field": f"parts[{part_index}].home_lang_text",
                        "issue": f"Chose '{chosen}' from alternatives: {options}; low confidence",
                        "suggestion": None,
                    })
            flagged += 1

        conn.commit()
        fixed += 1
        confident_str = "" if confident else " (flagged)"
        print(f"  {word_id}[{target_lang_text}]: {home_lang_text!r} → {chosen!r}{confident_str}")

    tracker.finish()
    conn.close()
    print(f"\nDone. {fixed} parts updated, {flagged} flagged for review.")


if __name__ == "__main__":
    main()
