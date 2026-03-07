#!/usr/bin/env python3
"""
Generate morpheme dictionary entries using the Claude API.

Results are written directly to the SQLite database (morpheme_dicts.db).
Interrupted runs can be resumed by re-running the same command — words
already in the DB are skipped automatically.

Usage:
    python generate_claude.py --input words.txt \
        --home English --target German --api-key sk-ant-...

    # Or set ANTHROPIC_API_KEY env var and omit --api-key:
    python generate_claude.py --input words.txt \
        --home English --target German
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import anthropic

from morpheme_db import get_db, split_pair, get_done_ids, insert_entry, get_known_issues_text, mark_word_done

# Allow MORPHEME_SORT_ANTHROPIC_API_KEY as a fallback for ANTHROPIC_API_KEY
if not os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("MORPHEME_SORT_ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = os.environ["MORPHEME_SORT_ANTHROPIC_API_KEY"]

PROMPTS_DIR = Path(__file__).parent / "prompts"
DEFAULT_BATCH_SIZE = 15
DEFAULT_MODEL = "claude-sonnet-4-6"

# ISO 639-1 codes for deriving the language-pair directory (e.g. "de-en").
# Falls back to the lowercased full name if not listed.
LANG_CODES = {
    "arabic": "ar",
    "chinese": "zh",
    "danish": "da",
    "dutch": "nl",
    "english": "en",
    "finnish": "fi",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "japanese": "ja",
    "korean": "ko",
    "mandarin": "zh",
    "norwegian": "no",
    "polish": "pl",
    "portuguese": "pt",
    "russian": "ru",
    "spanish": "es",
    "swedish": "sv",
    "turkish": "tr",
    "slovenian": "sl",
    "esperanto": "eo",
    "hindi": "hi",
    "swahili": "sw",
}


def lang_code(name: str) -> str:
    return LANG_CODES.get(name.lower(), name.lower())


def load_glossary(path: Path) -> tuple:
    """Parse glossary.txt (format: morpheme | short_gloss | homeLangDetails).

    Returns:
      prompt_text  -- concise 'morpheme = gloss' list to send to Claude
      details_dict -- maps normalized morpheme to full homeLangDetails text
    """
    prompt_lines = []
    details = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            prompt_lines.append(line)
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        morpheme, short_gloss = parts[0], parts[1]
        prompt_lines.append(f"{morpheme} = {short_gloss}")
        if len(parts) >= 3 and parts[2]:
            # Handle slash-separated multi-form entries like "-path-/-pathy"
            for variant in morpheme.split("/"):
                key = variant.strip().strip("-")
                if key:
                    details[key] = parts[2]
    return "\n".join(prompt_lines), details


def enrich_from_glossary(entries: list, details: dict) -> list:
    """Auto-fill homeLangDetails from glossary for parts that don't already have it."""
    for entry in entries:
        for part in entry.get("parts", []):
            if "homeLangDetails" not in part:
                key = part.get("targetLang", "").lower().strip("-")
                if key in details:
                    part["homeLangDetails"] = details[key]
    return entries


def load_prompt(filename: str, pair: str = None) -> str:
    """Load a prompt file, checking the language-pair subdirectory first."""
    if pair:
        pair_path = PROMPTS_DIR / pair / filename
        if pair_path.exists():
            return pair_path.read_text().strip()
    path = PROMPTS_DIR / filename
    if not path.exists():
        print(f"Error: Prompt file not found: {path}")
        sys.exit(1)
    return path.read_text().strip()


def fill_template(template: str, home: str, target: str, words: str = "") -> str:
    return (
        template
        .replace("<HOMELANG>", home)
        .replace("<TARGETLANG>", target)
        .replace("<WORDS>", words)
    )


def parse_response(text: str) -> list:
    """Parse the JSON response, stripping markdown fences if Claude added them."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text.rsplit("\n```", 1)[0].strip()
    return json.loads(text).get("words", [])


def main():
    parser = argparse.ArgumentParser(
        description="Generate morpheme dictionary entries using Claude",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", required=True, help="Word list file (one word per line)")
    parser.add_argument("--home", required=True, help="Home language, e.g. 'English'")
    parser.add_argument("--target", required=True, help="Target language, e.g. 'German'")
    parser.add_argument(
        "--api-key",
        default=None,
        help="Anthropic API key (falls back to ANTHROPIC_API_KEY env var if omitted)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Words per API call (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Claude model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to DB file (default: morpheme_dicts.db in project root)",
    )
    args = parser.parse_args()

    # Derive language codes and pair string (e.g. "de-en")
    target_lang = lang_code(args.target)
    home_lang = lang_code(args.home)
    pair = f"{target_lang}-{home_lang}"

    # Open DB
    conn = get_db(args.db)

    # Load prompts — pair-specific file wins over top-level fallback
    system = fill_template(load_prompt("system.txt", pair), args.home, args.target)
    user_template = load_prompt("user.txt", pair)

    # Load optional glossary from the pair directory
    glossary_path = PROMPTS_DIR / pair / "glossary.txt"
    if glossary_path.exists():
        glossary_prompt, glossary_details = load_glossary(glossary_path)
    else:
        glossary_prompt, glossary_details = "", {}

    # Load confirmed discrepancies from DB to avoid repeating known errors
    known_issues_text = get_known_issues_text(conn)

    # Load word list
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {args.input}")
        sys.exit(1)

    words = [
        line.strip()
        for line in input_path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    print(f"Loaded {len(words)} words from {args.input}")

    # Resume: skip words already present in the DB
    done_ids = get_done_ids(conn, target_lang, home_lang)
    remaining = [w for w in words if w.lower() not in done_ids]

    if done_ids:
        print(f"Resuming: {len(done_ids)} already done, {len(remaining)} remaining")
    if not remaining:
        print("All words already processed.")
        conn.close()
        return

    # Batch up the remaining words
    batches = [
        remaining[i : i + args.batch_size]
        for i in range(0, len(remaining), args.batch_size)
    ]

    # api_key=None makes anthropic fall back to ANTHROPIC_API_KEY env var
    client = anthropic.Anthropic(api_key=args.api_key)
    done_count = 0

    for batch_num, batch in enumerate(batches, 1):
        user_message = fill_template(user_template, args.home, args.target, "\n".join(batch))
        if glossary_prompt:
            user_message += f"\n\nCore morpheme glossary -- use these glosses consistently:\n{glossary_prompt}"
        if known_issues_text:
            user_message += f"\n\n{known_issues_text}"
        print(f"Batch {batch_num}/{len(batches)} ({len(batch)} words)...", end=" ", flush=True)

        try:
            response = client.messages.create(
                model=args.model,
                max_tokens=8192,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_message}],
            )
            cache_read = getattr(response.usage, "cache_read_input_tokens", 0)
            cache_status = f" [cache {'hit' if cache_read else 'miss'}]"
            entries = parse_response(response.content[0].text)
            if glossary_details:
                entries = enrich_from_glossary(entries, glossary_details)

            # Write each entry to DB immediately
            for entry in entries:
                insert_entry(conn, target_lang, home_lang, entry, source=args.input)

            # Mark each original batch word as done in the word list tracker
            for word in batch:
                mark_word_done(conn, target_lang, home_lang, word)

            done_count += len(entries)
            print(f"done ({done_count}/{len(remaining)}){cache_status}")

        except json.JSONDecodeError as e:
            print(f"PARSE ERROR: {e}")
            print(f"  Response was: {response.content[0].text[:300]}...")
            print("  Batch skipped — re-run to retry.")

        except anthropic.APIError as e:
            print(f"API ERROR: {e}")
            conn.close()
            sys.exit(1)

        if batch_num < len(batches):
            time.sleep(0.5)

    total = len(get_done_ids(conn, target_lang, home_lang))
    print(f"\nDone. {total} total entries in DB for [{target_lang}-{home_lang}]")
    conn.close()


if __name__ == "__main__":
    main()
