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
  8. translationShort not found (≥70% similarity) in exampleTranslation
  9. word_id not found (≥70% similarity) in exampleSentence
 10. Slash in homeLang (alternatives should be in homeLangDetails)
 11. homeLang longer than 2 words
 12. em-dash in homeLangDetails
 13. homeLangDetails opens with "From " (should be "As in ...")
 14. exampleTranslation/translationLong in wrong script (CJK/Cyrillic home langs)

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
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path


# Per-language fuzzy similarity thresholds (lower = more lenient = fewer false positives).
# Reasoning:
#   Agglutinative (fi, tr, sw, ko): suffixes stack heavily, surface forms diverge a lot → 0.55
#   Slavic (ru, pl, sl): heavy inflection, aspect pairs can have different roots → 0.58–0.60
#   Semitic (ar): trilateral root system, vowel patterns change across forms → 0.55
#   Germanic (de, nl, sv, no, da): compounds handled by substring; inflection moderate → 0.65
#   Romance (fr, es, it, pt): moderate inflection, endings change predictably → 0.68
#   South/East Asian (hi): Devanagari, some agglutination → 0.60
#   CJK (zh, ja): substring handles most cases; ja verb endings vary → 0.65
#   Regular/analytic (en, eo): minimal inflection, forms stay close → 0.72
LANG_THRESHOLDS: dict[str, float] = {
    "ar": 0.55,
    "da": 0.65,
    "de": 0.65,
    "en": 0.72,
    "eo": 0.70,
    "es": 0.68,
    "fi": 0.55,
    "fr": 0.68,
    "hi": 0.60,
    "it": 0.68,
    "ja": 0.65,
    "ko": 0.55,
    "nl": 0.65,
    "no": 0.65,
    "pl": 0.58,
    "pt": 0.68,
    "ru": 0.60,
    "sl": 0.58,
    "sv": 0.65,
    "sw": 0.55,
    "tr": 0.55,
    "zh": 0.80,  # Characters appear as-is; substring covers compounds
}
DEFAULT_THRESHOLD = 0.65


def _found_in_sentence(target: str, sentence: str, threshold: float = DEFAULT_THRESHOLD) -> bool:
    """Return True if target appears in sentence via substring match or fuzzy similarity.

    Handles multi-alternative targets like "freedom, liberty" or "to visit; to go" by
    splitting on common separators and checking each alternative independently.
    """
    # Split on comma/semicolon/slash separators to handle "freedom, liberty" etc.
    alternatives = [a.strip() for a in re.split(r"[,;/]", target) if a.strip()]
    if len(alternatives) > 1:
        return any(_found_in_sentence(alt, sentence, threshold) for alt in alternatives)

    t = target.lower().strip()
    # Strip parenthetical qualifiers like "(by phone)", "(f.)", "(transport)"
    t = re.sub(r"\s*\([^)]*\)", "", t).strip()
    # Strip leading "to " for English verb infinitives ("to visit" → "visit")
    if t.startswith("to "):
        t = t[3:]
    s = sentence.lower()
    # Substring match handles compounds (e.g. "Freiheit" inside "Meinungsfreiheit")
    if t in s:
        return True
    words = re.findall(r"\w+", s)
    if not words:
        return False
    return max(SequenceMatcher(None, t, w).ratio() for w in words) >= threshold


def _flag(word_id: str, category: str, field: str, issue: str) -> dict:
    return {"word_id": word_id, "category": category, "field": field, "issue": issue}


def check_entries(label: str, words: list, quiet: bool = False, conn=None, target_lang: str = "", home_lang: str = "") -> int:
    """Run sanity checks on a list of word entry dicts. Returns number of issues.

    If conn + target_lang + home_lang are provided, inserts new issues as
    verification_flags in the DB (skipping any already open for that word+category).
    """
    if not words:
        print(f"\n{label}: EMPTY — no entries")
        return 1

    issues = []  # list of flag dicts

    # Check for duplicate IDs (no word_id to attribute, use empty string)
    seen_ids = {}
    for i, w in enumerate(words):
        wid = w.get("id", "")
        if wid in seen_ids:
            issues.append(_flag("", "duplicate_id", "id",
                f"'{wid}' appears at index {seen_ids[wid]} and {i}"))
        else:
            seen_ids[wid] = i

    for w in words:
        wid = w.get("id", "")
        parts = w.get("parts", [])

        # 1. targetLang reconstruction
        target_parts = [p.get("targetLang", "") for p in parts]
        reconstructed = "".join(t.strip("-") for t in target_parts).lower()
        if reconstructed and reconstructed != wid.lower():
            issues.append(_flag(wid, "reconstruction", "parts",
                f"'{reconstructed}' != '{wid.lower()}' (parts: {' + '.join(repr(t) for t in target_parts)})"))

        # 2. Empty targetLang on a part
        for i, p in enumerate(parts):
            if not p.get("targetLang", ""):
                issues.append(_flag(wid, "empty_targetLang", "parts",
                    f"parts[{i}] has no targetLang"))

        # 3. Missing homeLang on non-trivial parts
        for i, p in enumerate(parts):
            tl = p.get("targetLang", "")
            if len(tl.strip("-")) > 1 and not p.get("homeLang", ""):
                issues.append(_flag(wid, "missing_homeLang", "parts",
                    f"parts[{i}] targetLang={repr(tl)} has no homeLang"))

        # 4. Circular homeLang
        for i, p in enumerate(parts):
            tl = p.get("targetLang", "").strip("-")
            hl = p.get("homeLang", "")
            if hl.startswith("-") and hl.endswith("-") and len(hl) > 2:
                issues.append(_flag(wid, "circular_homeLang", "parts",
                    f"parts[{i}] homeLang={repr(hl)} (dash-wrapped label)"))
            elif hl == tl and len(tl) > 2 and "-" not in hl:
                if not any(c in "aeiouAEIOU" for c in tl):
                    issues.append(_flag(wid, "circular_homeLang", "parts",
                        f"parts[{i}] homeLang={repr(hl)} == targetLang (no gloss)"))

        # 5. Empty required fields
        if not w.get("translationShort", "").strip():
            issues.append(_flag(wid, "missing_field", "translationShort", "translationShort is empty"))
        if not w.get("exampleSentence", "").strip():
            issues.append(_flag(wid, "missing_field", "exampleSentence", "exampleSentence is empty"))

        # 6. Suspiciously few or many parts
        if len(parts) == 0:
            issues.append(_flag(wid, "bad_parts", "parts", "has 0 parts"))
        elif len(parts) > 5:
            issues.append(_flag(wid, "bad_parts", "parts", f"has {len(parts)} parts (may be over-split)"))

        # 8. translationShort should appear (fuzzy or substring) in exampleTranslation
        #    Both fields are in home_lang → use home_lang threshold
        trans_short = w.get("translationShort", "").strip()
        ex_trans = w.get("exampleTranslation", "").strip()
        home_thresh = LANG_THRESHOLDS.get(home_lang, DEFAULT_THRESHOLD)
        if trans_short and ex_trans and len(trans_short) >= 4:
            if not _found_in_sentence(trans_short, ex_trans, threshold=home_thresh):
                issues.append(_flag(wid, "translation_mismatch", "translationShort",
                    f"translationShort={repr(trans_short)} not found in exampleTranslation | {ex_trans}"))

        # 9. word_id should appear (fuzzy or substring) in exampleSentence
        #    Both fields are in target_lang → use target_lang threshold
        ex_sent = w.get("exampleSentence", "").strip()
        target_thresh = LANG_THRESHOLDS.get(target_lang, DEFAULT_THRESHOLD)
        if wid and ex_sent and len(wid) >= 4:
            if not _found_in_sentence(wid, ex_sent, threshold=target_thresh):
                issues.append(_flag(wid, "word_not_in_example", "exampleSentence",
                    f"word_id not found in exampleSentence"))

        # 10. Slash in homeLang (alternatives should go in homeLangDetails)
        for i, p in enumerate(parts):
            hl = p.get("homeLang", "")
            if "/" in hl and hl not in ("-",):
                issues.append(_flag(wid, "slash_in_homeLang", f"parts[{i}].homeLang",
                    f"parts[{i}] homeLang={repr(hl)} contains slash — pick one, put rest in homeLangDetails"))

        # 11. homeLang longer than 2 words (excluding dash-notation and parenthetical labels)
        for i, p in enumerate(parts):
            hl = p.get("homeLang", "").strip()
            if hl.startswith("-") or (hl.startswith("(") and hl.endswith(")")):
                continue
            word_count = len(hl.split())
            if word_count > 2:
                issues.append(_flag(wid, "homeLang_too_long", f"parts[{i}].homeLang",
                    f"parts[{i}] homeLang={repr(hl)} is {word_count} words (max 2)"))

        # 12. em-dash in homeLangDetails
        for i, p in enumerate(parts):
            details = p.get("homeLangDetails", "") or ""
            if "\u2014" in details or " -- " in details:
                issues.append(_flag(wid, "emdash_in_details", f"parts[{i}].homeLangDetails",
                    f"parts[{i}] homeLangDetails contains em-dash (use commas instead)"))

        # 13. homeLangDetails opens with "From "
        for i, p in enumerate(parts):
            details = (p.get("homeLangDetails", "") or "").strip()
            if re.match(r"^From\s+\w", details):
                issues.append(_flag(wid, "from_opening", f"parts[{i}].homeLangDetails",
                    f"parts[{i}] homeLangDetails opens with 'From ...' (use 'As in ...' instead)"))

        # 14. exampleTranslation / translationLong in wrong script for CJK/Cyrillic home langs
        CJK_RANGE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")  # CJK + hiragana/katakana
        CYRILLIC = re.compile(r"[\u0400-\u04ff]")
        if home_lang in ("zh",):
            for field in ("exampleTranslation", "translationLong"):
                val = w.get(field, "").strip()
                if val and not CJK_RANGE.search(val):
                    issues.append(_flag(wid, "wrong_script", field,
                        f"{field} appears to be in wrong language (no CJK characters, home_lang={home_lang})"))
        elif home_lang in ("ja",):
            for field in ("exampleTranslation", "translationLong"):
                val = w.get(field, "").strip()
                if val and not CJK_RANGE.search(val):
                    issues.append(_flag(wid, "wrong_script", field,
                        f"{field} appears to be in wrong language (no Japanese characters, home_lang={home_lang})"))
        elif home_lang in ("ru",):
            for field in ("exampleTranslation", "translationLong"):
                val = w.get(field, "").strip()
                if val and not CYRILLIC.search(val):
                    issues.append(_flag(wid, "wrong_script", field,
                        f"{field} appears to be in wrong language (no Cyrillic characters, home_lang={home_lang})"))

    if issues:
        print(f"\n{label}: {len(words)} entries, {len(issues)} issues")
        for f in issues:
            print(f"  [{f['category']}] [{f['word_id']}] {f['issue']}")
    elif not quiet:
        print(f"{label}: {len(words)} entries — OK")

    # Insert new flags into DB if in DB mode
    if conn and target_lang and home_lang and issues:
        from morpheme_db import insert_flag
        # Build set of already-open flags to avoid duplicates
        existing = set(conn.execute(
            "SELECT word_id, category FROM verification_flags WHERE target_lang=? AND home_lang=? AND status='open'",
            (target_lang, home_lang),
        ).fetchall())
        inserted = 0
        for f in issues:
            if f["word_id"] and (f["word_id"], f["category"]) not in existing:
                insert_flag(conn, target_lang, home_lang, f["word_id"], f)
                existing.add((f["word_id"], f["category"]))
                inserted += 1
        if inserted:
            print(f"  {inserted} new flags inserted into DB")

    return len(issues)


def check_json_file(path: Path, quiet: bool = False) -> int:
    """Check a single JSON dict file. Returns number of issues."""
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        print(f"\n{path.name}: ERROR reading file: {e}")
        return 1
    return check_entries(path.name, data.get("words", []), quiet=quiet)


def check_db_pair(conn, target_lang: str, home_lang: str, quiet: bool = False, all_entries: bool = False, word_set: str = None) -> int:
    """Check a single lang pair from the DB. Returns number of issues."""
    from morpheme_db import get_entries
    label = f"{target_lang}-{home_lang} (DB)"
    entries = get_entries(conn, target_lang, home_lang, all_entries=all_entries, word_set=word_set)
    return check_entries(label, entries, quiet=quiet, conn=conn, target_lang=target_lang, home_lang=home_lang)


def main():
    parser = argparse.ArgumentParser(description="Sanity-check morpheme dict entries")
    parser.add_argument("files", nargs="*", help="Dict JSON files to check (implies --pattern mode)")
    parser.add_argument("--pattern", default=None, help="Glob pattern, e.g. 'dicts/*-en.json'")
    parser.add_argument("--db", action="store_true", help="Read from DB instead of JSON files (default)")
    parser.add_argument("--target-lang", metavar="CODE", help="Check a single DB pair (use with --home-lang)")
    parser.add_argument("--home-lang", metavar="CODE", help="Home language code for single-pair DB check")
    parser.add_argument("--db-path", default=None, help="Path to DB file")
    parser.add_argument("--quiet", action="store_true", help="Only show entries/pairs with issues")
    parser.add_argument("--all", dest="all_entries", action="store_true", help="Check all entries regardless of import/to_verify status")
    parser.add_argument("--word-set", metavar="NAME", default=None, help="Only check entries with this word_set value")
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
        total_issues += check_db_pair(conn, args.target_lang, args.home_lang, quiet=args.quiet, all_entries=args.all_entries, word_set=args.word_set)
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
                total_issues += check_db_pair(conn, target_lang, home_lang, quiet=args.quiet, all_entries=args.all_entries, word_set=args.word_set)
        conn.close()

    print(f"\nTotal: {total_issues} issues")
    sys.exit(0 if total_issues == 0 else 1)


if __name__ == "__main__":
    main()
