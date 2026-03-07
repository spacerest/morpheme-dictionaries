#!/usr/bin/env python3
"""
Fast programmatic sanity checks on morpheme dict entries.
Catches structural issues before spending API calls on LLM verify.

Checks performed:
  1. targetLang parts concatenate back to the word ID
  2. Missing homeLang on non-trivial parts
  3. Circular homeLang (morpheme used as its own gloss, e.g. -morph-)
  4. Empty required fields (translationShort, exampleSentence)
  5. Suspiciously few or many parts
  6. Duplicate word IDs
  7. Parts with empty targetLang

Usage:
    python sanity_check.py                        # all pairs in DB (default)
    python sanity_check.py --db                   # explicitly use DB
    python sanity_check.py dicts/de-en.json       # specific JSON file (--pattern mode)
    python sanity_check.py --pattern "dicts/*-en.json"
    python sanity_check.py --target-lang de --home-lang en  # single DB pair
    python sanity_check.py --quiet                # only show files/pairs with issues
"""

import argparse
import json
import sys
from pathlib import Path


def check_entries(label: str, words: list, quiet: bool = False) -> int:
    """Run sanity checks on a list of word entry dicts. Returns number of issues."""
    if not words:
        print(f"\n{label}: EMPTY — no entries")
        return 1

    issues = []

    # Check for duplicate IDs
    seen_ids = {}
    for i, w in enumerate(words):
        wid = w.get("id", "")
        if wid in seen_ids:
            issues.append(f"  [duplicate_id] '{wid}' appears at index {seen_ids[wid]} and {i}")
        else:
            seen_ids[wid] = i

    for w in words:
        wid = w.get("id", "")
        parts = w.get("parts", [])
        label_word = f"[{wid}]"

        # 1. targetLang reconstruction
        target_parts = [p.get("targetLang", "") for p in parts]
        reconstructed = "".join(t.strip("-") for t in target_parts).lower()
        word_lower = wid.lower()
        if reconstructed and reconstructed != word_lower:
            issues.append(
                f"  [reconstruction] {label_word} '{reconstructed}' != '{word_lower}'"
                f"  (parts: {' + '.join(repr(t) for t in target_parts)})"
            )

        # 2. Empty targetLang on a part
        for i, p in enumerate(parts):
            tl = p.get("targetLang", "")
            if not tl:
                issues.append(f"  [empty_targetLang] {label_word} parts[{i}] has no targetLang")

        # 3. Missing homeLang on non-trivial parts (targetLang longer than 1 char)
        for i, p in enumerate(parts):
            tl = p.get("targetLang", "")
            hl = p.get("homeLang", "")
            if len(tl.strip("-")) > 1 and not hl:
                issues.append(f"  [missing_homeLang] {label_word} parts[{i}] targetLang={repr(tl)} has no homeLang")

        # 4. Circular homeLang: morpheme used as its own gloss
        for i, p in enumerate(parts):
            tl = p.get("targetLang", "").strip("-")
            hl = p.get("homeLang", "")
            if hl.startswith("-") and hl.endswith("-") and len(hl) > 2:
                issues.append(f"  [circular_homelang] {label_word} parts[{i}] homeLang={repr(hl)} (dash-wrapped label)")
            elif hl == tl and len(tl) > 2 and "-" not in hl:
                has_vowel = any(c in "aeiouAEIOU" for c in tl)
                if not has_vowel:
                    issues.append(f"  [circular_homelang] {label_word} parts[{i}] homeLang={repr(hl)} == targetLang (no gloss)")

        # 5. Empty required fields
        if not w.get("translationShort", "").strip():
            issues.append(f"  [missing_field] {label_word} translationShort is empty")
        if not w.get("exampleSentence", "").strip():
            issues.append(f"  [missing_field] {label_word} exampleSentence is empty")

        # 6. Suspiciously few or many parts
        if len(parts) == 0:
            issues.append(f"  [bad_parts] {label_word} has 0 parts")
        elif len(parts) > 5:
            issues.append(f"  [bad_parts] {label_word} has {len(parts)} parts (may be over-split)")

    if issues:
        print(f"\n{label}: {len(words)} entries, {len(issues)} issues")
        for issue in issues:
            print(issue)
    elif not quiet:
        print(f"{label}: {len(words)} entries — OK")

    return len(issues)


def check_json_file(path: Path, quiet: bool = False) -> int:
    """Check a single JSON dict file. Returns number of issues."""
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        print(f"\n{path.name}: ERROR reading file: {e}")
        return 1
    return check_entries(path.name, data.get("words", []), quiet=quiet)


def check_db_pair(conn, target_lang: str, home_lang: str, quiet: bool = False) -> int:
    """Check a single lang pair from the DB. Returns number of issues."""
    from morpheme_db import get_entries
    label = f"{target_lang}-{home_lang} (DB)"
    entries = get_entries(conn, target_lang, home_lang)
    return check_entries(label, entries, quiet=quiet)


def main():
    parser = argparse.ArgumentParser(description="Sanity-check morpheme dict entries")
    parser.add_argument("files", nargs="*", help="Dict JSON files to check (implies --pattern mode)")
    parser.add_argument("--pattern", default=None, help="Glob pattern, e.g. 'dicts/*-en.json'")
    parser.add_argument("--db", action="store_true", help="Read from DB instead of JSON files (default)")
    parser.add_argument("--target-lang", metavar="CODE", help="Check a single DB pair (use with --home-lang)")
    parser.add_argument("--home-lang", metavar="CODE", help="Home language code for single-pair DB check")
    parser.add_argument("--db-path", default=None, help="Path to DB file")
    parser.add_argument("--quiet", action="store_true", help="Only show entries/pairs with issues")
    args = parser.parse_args()

    total_issues = 0

    # Determine mode
    use_json = bool(args.files or args.pattern)
    use_db_single = bool(args.target_lang)

    if use_json:
        # JSON file mode (backward compat)
        if args.files:
            paths = [Path(f) for f in args.files]
        else:
            paths = sorted(Path(".").glob(args.pattern))
        if not paths:
            print("No dict files found.")
            sys.exit(1)
        for path in paths:
            total_issues += check_json_file(path, quiet=args.quiet)

    elif use_db_single:
        if not args.home_lang:
            parser.error("--home-lang is required when --target-lang is given")
        from morpheme_db import get_db
        conn = get_db(args.db_path)
        total_issues += check_db_pair(conn, args.target_lang, args.home_lang, quiet=args.quiet)
        conn.close()

    else:
        # Default: DB mode — check all pairs
        from morpheme_db import get_db, get_all_pairs
        conn = get_db(args.db_path)
        pairs = get_all_pairs(conn)
        if not pairs:
            # Fall back to JSON files if DB is empty
            paths = sorted(Path("dicts").glob("*.json"))
            if not paths:
                print("No dict files or DB entries found.")
                sys.exit(1)
            for path in paths:
                total_issues += check_json_file(path, quiet=args.quiet)
        else:
            for target_lang, home_lang in pairs:
                total_issues += check_db_pair(conn, target_lang, home_lang, quiet=args.quiet)
        conn.close()

    print(f"\nTotal: {total_issues} issues")
    sys.exit(0 if total_issues == 0 else 1)


if __name__ == "__main__":
    main()
