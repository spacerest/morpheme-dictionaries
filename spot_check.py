#!/usr/bin/env python3
"""
Quick spot-check of generated dictionaries.
Shows the first entry of each dict with its morpheme breakdown.

Usage:
    python spot_check.py              # check all dicts/
    python spot_check.py dicts/de-en.json dicts/fr-en.json
    python spot_check.py --pattern "dicts/*-en.json"
"""

import argparse
import json
import sys
from pathlib import Path


def check_dict(path: Path):
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        print(f"{path.name}: ERROR reading file: {e}")
        return

    words = data.get("words", [])
    if not words:
        print(f"{path.name}: EMPTY")
        return

    w = words[0]
    parts = [(p.get("targetLang", ""), p.get("homeLang", "")) for p in w.get("parts", [])]
    print(f"{path.name}: {len(words)} entries")
    print(f"  [{w['id']}] {' + '.join(f'{tl}={hl}' for tl, hl in parts)}")
    print(f"  \"{w.get('translationShort', '')}\" — {w.get('exampleSentence', '')}")
    print(f"  -> {w.get('exampleTranslation', '')}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Spot-check generated morpheme dicts")
    parser.add_argument("files", nargs="*", help="Dict files to check (default: all dicts/*.json)")
    parser.add_argument("--pattern", default="dicts/*.json", help="Glob pattern (default: dicts/*.json)")
    args = parser.parse_args()

    if args.files:
        paths = [Path(f) for f in args.files]
    else:
        paths = sorted(Path("dicts").glob("*.json"))

    if not paths:
        print("No dict files found.")
        sys.exit(1)

    for path in paths:
        check_dict(path)


if __name__ == "__main__":
    main()
