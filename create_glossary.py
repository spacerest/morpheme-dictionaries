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

PROMPTS_DIR = Path(__file__).parent / "prompts"
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


def load_existing_morphemes(glossary_path: Path) -> list[str]:
    """Return list of morpheme keys already in the glossary, for dedup."""
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

    # Load existing morphemes for deduplication
    existing = load_existing_morphemes(glossary_path)
    if existing:
        print(f"Found {len(existing)} existing morphemes in {glossary_path}")

    # Build and send prompt
    template = load_prompt_template()
    prompt = build_prompt(template, target_code, home_code, args.count, existing)

    print(f"Generating {args.count} morphemes for {target_name} (home: {home_name}) using {args.model}...")

    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model=args.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
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

    append_to_glossary(glossary_path, new_lines)
    print(f"\nDone. Glossary at: {glossary_path}")
    print("Review the file before using it — edit any entries that need adjusting.")


if __name__ == "__main__":
    main()
