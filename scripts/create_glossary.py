#!/usr/bin/env python3
"""
Generate or extend a morpheme glossary for a language pair using Claude.

The glossary is saved to prompts/{pair}/glossary.txt in the format:
    morpheme | short_gloss | homeLangDetails

If the glossary already exists, new entries are appended (existing morphemes
are passed to Claude so they aren't duplicated).

Usage:
    python create_glossary.py --pair de-en --count 30
    python create_glossary.py --pair en-ja --count 20
    python create_glossary.py --pair de-en --count 15 --model claude-opus-4-6

Pair format: {target-lang-code}-{home-lang-code}, e.g.:
    de-en  →  learning German, home=English
    en-de  →  learning English, home=German
    ja-en  →  learning Japanese, home=English
"""

import argparse
import os
import sys
import time
from pathlib import Path

import anthropic

from cost_tracker import CostTracker
from morpheme_db import get_db, get_morphemes, upsert_morphemes

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
DEFAULT_MODEL = "claude-sonnet-4-6"

LANG_NAMES = {
    "ar": "Arabic",
    "da": "Danish",
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fi": "Finnish",
    "fr": "French",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ru": "Russian",
    "sl": "Slovenian",
    "sv": "Swedish",
    "tr": "Turkish",
    "zh": "Mandarin Chinese",
    "ga": "Irish Gaelic",
}


def lang_name(code: str) -> str:
    return LANG_NAMES.get(code.lower(), code.upper())


def parse_pair(pair: str) -> tuple[str, str]:
    """Split 'de-en' into (target='de', home='en')."""
    parts = pair.split("-")
    if len(parts) != 2:
        print(f"ERROR: pair must be two ISO codes separated by a dash, e.g. 'de-en'. Got: {pair!r}")
        sys.exit(1)
    return parts[0], parts[1]


def load_existing_morphemes(conn, target_code: str, home_code: str, glossary_path: Path) -> list[str]:
    """Return morpheme keys already recorded, using DB as source of truth.

    Falls back to reading the file if the DB has no entries yet (e.g. first run
    before the DB was wired up).
    """
    db_rows = get_morphemes(conn, target_code, home_code)
    if db_rows:
        return [r["morpheme"] for r in db_rows]
    # fallback: parse the file
    if not glossary_path.exists():
        return []
    morphemes = []
    for line in glossary_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key = line.split("|")[0].strip()
        if key:
            morphemes.append(key)
    return morphemes


def parse_glossary_lines(lines: list[str]) -> list[dict]:
    """Parse 'morpheme | short_gloss | homeLangDetails' lines into dicts."""
    result = []
    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        result.append({
            "morpheme": parts[0],
            "short_gloss": parts[1],
            "home_lang_details": parts[2] if len(parts) >= 3 else None,
        })
    return result


def load_prompt_template() -> str:
    path = PROMPTS_DIR / "create_glossary.txt"
    if not path.exists():
        print(f"ERROR: prompt template not found at {path}")
        sys.exit(1)
    return path.read_text()


def build_prompt(template: str, target: str, home: str, count: int, existing: list[str]) -> str:
    if existing:
        existing_lines = "\n".join(f"  {m}" for m in existing)
        existing_section = (
            f"The following morphemes are already in the glossary — do not repeat them:\n"
            f"{existing_lines}\n\n"
        )
    else:
        existing_section = ""

    return (
        template
        .replace("<TARGETLANG>", lang_name(target))
        .replace("<HOMELANG>", lang_name(home))
        .replace("<COUNT>", str(count))
        .replace("<EXISTING_SECTION>", existing_section)
    )


def parse_glossary_text(text: str) -> list[str]:
    """Return cleaned non-empty lines from the model's glossary response."""
    lines = []
    for line in text.strip().splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(stripped)
    return lines


def append_to_glossary(glossary_path: Path, new_lines: list[str]):
    """Append new lines to the glossary file (creates it if it doesn't exist)."""
    glossary_path.parent.mkdir(parents=True, exist_ok=True)

    if not glossary_path.exists():
        glossary_path.write_text("\n".join(new_lines) + "\n")
        print(f"Created {glossary_path} with {len(new_lines)} lines")
    else:
        existing = glossary_path.read_text()
        separator = "\n" if existing.endswith("\n") else "\n\n"
        glossary_path.write_text(existing + separator + "\n".join(new_lines) + "\n")
        print(f"Appended {len(new_lines)} lines to {glossary_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate or extend a morpheme glossary for a language pair",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--pair", required=True,
        help="Language pair code, e.g. 'de-en' (target-home). Use ISO 639-1 codes."
    )
    parser.add_argument(
        "--count", type=int, default=20,
        help="Number of new morphemes to generate (default: 20)"
    )
    parser.add_argument(
        "--output", default=None,
        help="Output file path (default: prompts/{pair}/glossary.txt)"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Claude model to use (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--api-key", default=None,
        help="Anthropic API key (falls back to ANTHROPIC_API_KEY / MORPHEME_SORT_ANTHROPIC_API_KEY)"
    )
    parser.add_argument(
        "--db", default=None,
        help="Path to DB file (default: morpheme_dicts.db in project root)"
    )
    args = parser.parse_args()

    # Resolve API key
    api_key = args.api_key
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("MORPHEME_SORT_ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: No API key found. Set ANTHROPIC_API_KEY or pass --api-key.")
        sys.exit(1)

    target_code, home_code = parse_pair(args.pair)
    target_name = lang_name(target_code)
    home_name = lang_name(home_code)

    glossary_path = Path(args.output) if args.output else PROMPTS_DIR / args.pair / "glossary.txt"

    conn = get_db(args.db)

    # Load existing morphemes for deduplication (DB-first)
    existing = load_existing_morphemes(conn, target_code, home_code, glossary_path)
    if existing:
        print(f"Found {len(existing)} existing morphemes")

    # Build and send prompt
    template = load_prompt_template()
    prompt = build_prompt(template, target_code, home_code, args.count, existing)

    print(f"Generating {args.count} morphemes for {target_name} (home: {home_name}) using {args.model}...")

    client = anthropic.Anthropic(api_key=api_key)
    tracker = CostTracker(script="create_glossary", pair=args.pair, model=args.model)

    try:
        response = client.messages.create(
            model=args.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        tracker.add(response.usage)
        text = response.content[0].text
    except anthropic.APIError as e:
        print(f"API ERROR: {e}")
        sys.exit(1)

    new_lines = parse_glossary_text(text)
    entry_count = sum(1 for l in new_lines if l and not l.startswith("#"))
    print(f"Received {entry_count} new morpheme entries")

    if not new_lines:
        print("ERROR: Empty response from model.")
        sys.exit(1)

    tracker.finish()
    append_to_glossary(glossary_path, new_lines)

    morpheme_dicts = parse_glossary_lines(new_lines)
    written = upsert_morphemes(conn, target_code, home_code, morpheme_dicts)
    conn.close()
    print(f"Synced {written} morphemes to DB")

    print(f"\nDone. Glossary at: {glossary_path}")
    print("Review the file before using it — edit any entries that need adjusting.")


if __name__ == "__main__":
    main()
