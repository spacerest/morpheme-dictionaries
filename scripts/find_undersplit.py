#!/usr/bin/env python3
"""
Find entries where the morpheme breakdown might not be granular enough —
i.e. a targetLang part is itself a compound that could be split further.

Two checks:

1. Self-referential (all pairs, no extra dependencies):
   If a part's targetLang (stripped of hyphens) matches another word_id in
   the same pair that has ≥2 parts, that part could probably be split further.
   Also tries common Fugen-suffix variants (trailing s/n/en/e/es/er).

2. CharSplit (German only, requires: pip install charsplit):
   Tries to compound-split each substantial part independently.
   Run with --charsplit to enable.

After the candidate report, --populate-morphemes inserts any targetLang parts
that appear in the entries table but are missing from the morphemes table,
using the most common homeLang gloss for that part as the short_gloss.

Usage:
    python find_undersplit.py                         # all pairs
    python find_undersplit.py --pair de-en            # one pair
    python find_undersplit.py --pair de-en --charsplit
    python find_undersplit.py --populate-morphemes    # sync parts → morphemes table
    python find_undersplit.py --pair de-en --populate-morphemes --charsplit
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

# Fugen suffixes to try stripping when looking for a match
_FUGEN = ("en", "es", "er", "ns", "s", "n", "e")


def _variants(text: str) -> list:
    """Return lowercase stripped form + common Fugen-stripped variants."""
    base = text.strip("-").lower()
    results = [base]
    for suffix in _FUGEN:
        if base.endswith(suffix) and len(base) - len(suffix) >= 4:
            results.append(base[: -len(suffix)])
    return results


def run_selfref_check(conn, target_lang: str, home_lang: str) -> list:
    """Return list of candidate dicts for entries with potentially under-split parts."""
    # Build lookup: lowercased word_id → number of parts
    word_part_counts = dict(
        conn.execute(
            """SELECT LOWER(e.word_id), COUNT(p.part_index)
               FROM entries e
               JOIN parts p USING (target_lang, home_lang, word_id)
               WHERE e.target_lang=? AND e.home_lang=?
               GROUP BY e.word_id""",
            (target_lang, home_lang),
        ).fetchall()
    )

    # Fetch all entries with their parts
    rows = conn.execute(
        """SELECT e.word_id, e.translation_short, e.review_status,
                  p.part_index, p.target_lang_text, p.home_lang_text
           FROM entries e
           JOIN parts p USING (target_lang, home_lang, word_id)
           WHERE e.target_lang=? AND e.home_lang=?
           ORDER BY e.rowid, p.part_index""",
        (target_lang, home_lang),
    ).fetchall()

    # Group by entry
    entries: dict = {}
    for row in rows:
        wid = row["word_id"]
        if wid not in entries:
            entries[wid] = {
                "word_id": wid,
                "translation_short": row["translation_short"],
                "review_status": row["review_status"],
                "parts": [],
            }
        entries[wid]["parts"].append({
            "targetLang": row["target_lang_text"],
            "homeLang": row["home_lang_text"],
        })

    candidates = []
    for wid, entry in entries.items():
        hits = []
        for part in entry["parts"]:
            tl = part["targetLang"]
            stripped = tl.strip("-")
            if len(stripped) < 4:
                continue  # Too short to be a meaningful compound part
            for variant in _variants(tl):
                if variant in word_part_counts and word_part_counts[variant] >= 2:
                    # Found a part that is itself a word with ≥2 parts
                    hits.append({
                        "part": tl,
                        "matched_word_id": variant,
                        "matched_part_count": word_part_counts[variant],
                    })
                    break  # Only report once per part
        if hits:
            candidates.append({**entry, "hits": hits})

    return candidates


def run_charsplit_check(conn, target_lang: str, home_lang: str) -> list:
    """Return candidate dicts for entries where CharSplit can split a part further."""
    try:
        from charsplit import Splitter
    except ImportError:
        print("charsplit not installed — run: pip install charsplit")
        return []

    splitter = Splitter()

    rows = conn.execute(
        """SELECT e.word_id, e.translation_short, e.review_status,
                  p.part_index, p.target_lang_text, p.home_lang_text
           FROM entries e
           JOIN parts p USING (target_lang, home_lang, word_id)
           WHERE e.target_lang=? AND e.home_lang=?
           ORDER BY e.rowid, p.part_index""",
        (target_lang, home_lang),
    ).fetchall()

    entries: dict = {}
    for row in rows:
        wid = row["word_id"]
        if wid not in entries:
            entries[wid] = {
                "word_id": wid,
                "translation_short": row["translation_short"],
                "review_status": row["review_status"],
                "parts": [],
            }
        entries[wid]["parts"].append({
            "targetLang": row["target_lang_text"],
            "homeLang": row["home_lang_text"],
        })

    candidates = []
    for wid, entry in entries.items():
        hits = []
        for part in entry["parts"]:
            stripped = part["targetLang"].strip("-")
            if len(stripped) < 6:
                continue
            splits = splitter.split_compound(stripped)
            # split_compound returns list of (left, right, score) tuples
            if splits and splits[0][2] > 0.5 and splits[0][0] and splits[0][1]:
                left, right, score = splits[0]
                hits.append({
                    "part": part["targetLang"],
                    "suggested_split": f"{left} + {right}",
                    "confidence": round(score, 2),
                })
        if hits:
            candidates.append({**entry, "hits": hits})

    return candidates


def populate_morphemes(conn, target_lang: str, home_lang: str, dry_run: bool = False) -> int:
    """Insert parts from entries that are missing from the morphemes table.

    Uses the most frequently occurring homeLang gloss for each targetLang text.
    Marks inserted rows with a note that they came from the smallest-parts check.
    Returns number of morphemes inserted.
    """
    # Find all (targetLang, homeLang) pairs used in parts, with frequency
    rows = conn.execute(
        """SELECT p.target_lang_text, p.home_lang_text, COUNT(*) as freq
           FROM parts p
           WHERE p.target_lang=? AND p.home_lang=?
             AND LENGTH(TRIM(p.target_lang_text, '-')) >= 2
           GROUP BY p.target_lang_text, p.home_lang_text
           ORDER BY p.target_lang_text, freq DESC""",
        (target_lang, home_lang),
    ).fetchall()

    # For each targetLang text, keep the most frequent homeLang gloss
    best_gloss: dict = {}
    for row in rows:
        tl = row["target_lang_text"]
        if tl not in best_gloss:
            best_gloss[tl] = row["home_lang_text"]

    # Find which ones are already in morphemes
    existing = {
        row["morpheme"]
        for row in conn.execute(
            "SELECT morpheme FROM morphemes WHERE target_lang=? AND home_lang=?",
            (target_lang, home_lang),
        ).fetchall()
    }

    inserted = 0
    note = "Added by find_undersplit.py — appears in entries but has no glossary entry. Verify and expand."

    for morpheme, short_gloss in best_gloss.items():
        if morpheme in existing:
            continue
        if not dry_run:
            conn.execute(
                """INSERT OR IGNORE INTO morphemes
                   (target_lang, home_lang, morpheme, short_gloss, home_lang_details)
                   VALUES (?, ?, ?, ?, ?)""",
                (target_lang, home_lang, morpheme, short_gloss, note),
            )
        inserted += 1

    if not dry_run:
        conn.commit()

    return inserted


def print_candidates(candidates: list, pair: str, check_name: str):
    if not candidates:
        print(f"  [{pair}] {check_name}: no candidates found")
        return

    already_passed = sum(1 for c in candidates if c.get("review_status") == "passed")
    print(f"\n  [{pair}] {check_name}: {len(candidates)} candidates "
          f"({already_passed} already marked 'passed')")
    print()

    for c in candidates:
        status_tag = f" [review_status={c['review_status']}]" if c.get("review_status") else ""
        parts_str = " + ".join(
            p["targetLang"] + "(" + p["homeLang"] + ")" for p in c["parts"]
        )
        print(f"  {c['word_id']}{status_tag}")
        print(f"    breakdown:   {parts_str}")
        print(f"    translation: {c['translation_short']}")
        for hit in c["hits"]:
            if "matched_word_id" in hit:
                print(f"    !! part '{hit['part']}' matches word '{hit['matched_word_id']}'"
                      f" ({hit['matched_part_count']} parts)")
            else:
                print(f"    !! part '{hit['part']}' → CharSplit suggests: "
                      f"{hit['suggested_split']} (confidence {hit['confidence']})")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Find entries where the morpheme breakdown could be more granular",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--pair", metavar="XX-YY",
        help="Check a single lang pair (e.g. de-en). Default: all pairs.",
    )
    parser.add_argument(
        "--charsplit", action="store_true",
        help="Also run CharSplit compound check (German-focused; pip install charsplit)",
    )
    parser.add_argument(
        "--populate-morphemes", action="store_true",
        help="Insert parts missing from the morphemes table into it, with a note.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="With --populate-morphemes: show what would be inserted without writing.",
    )
    parser.add_argument("--db", default=None, help="Path to DB file")
    args = parser.parse_args()

    from morpheme_db import get_db, split_pair, get_all_pairs

    conn = get_db(args.db)

    if args.pair:
        pairs = [split_pair(args.pair)]
    else:
        pairs = get_all_pairs(conn)

    total_selfref = 0
    total_charsplit = 0
    total_morphemes_added = 0

    for target_lang, home_lang in pairs:
        pair = f"{target_lang}-{home_lang}"

        # Self-referential check
        candidates = run_selfref_check(conn, target_lang, home_lang)
        print_candidates(candidates, pair, "self-referential")
        total_selfref += len(candidates)

        # CharSplit check (German only by default, but user can try others)
        if args.charsplit:
            cs_candidates = run_charsplit_check(conn, target_lang, home_lang)
            # Deduplicate: remove entries already caught by self-ref
            selfref_ids = {c["word_id"] for c in candidates}
            new_cs = [c for c in cs_candidates if c["word_id"] not in selfref_ids]
            print_candidates(new_cs, pair, "CharSplit (additional)")
            total_charsplit += len(new_cs)

        # Populate morphemes
        if args.populate_morphemes:
            n = populate_morphemes(conn, target_lang, home_lang, dry_run=args.dry_run)
            action = "would insert" if args.dry_run else "inserted"
            print(f"  [{pair}] morphemes: {action} {n} missing entries into morphemes table")
            total_morphemes_added += n

    print(f"\n=== Summary ===")
    print(f"Self-referential candidates: {total_selfref}")
    if args.charsplit:
        print(f"CharSplit additional candidates: {total_charsplit}")
    if args.populate_morphemes:
        label = "would add" if args.dry_run else "added"
        print(f"Morphemes {label}: {total_morphemes_added}")

    conn.close()


if __name__ == "__main__":
    main()
