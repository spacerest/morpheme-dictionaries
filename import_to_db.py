#!/usr/bin/env python3
"""
One-time import: load all existing JSON dicts, review files, glossaries,
and word lists into the SQLite database.

Sources imported (in order):
  1. dicts/*.json                (working generation output; excludes *-flagged.json)
  2. App assets *.json           (../water_sorting_word_roots/app/src/main/assets/)
  3. review/discrepancies.json   → known_discrepancies table
  4. review/flagged-*.json       → verification_flags table
  5. prompts/*/glossary.txt      → morphemes table
  6. word-lists/*.txt            → wordlist_words table (words already in entries → 'done')

Conflict strategy (same target_lang + home_lang + word_id in both sources):
  - Keep whichever entry has more parts.
  - Tie-break: keep the one with longer total homeLangDetails text.
  - Default (no --replace): keep existing DB entry, log skipped.
  - With --replace: always overwrite.

Usage:
    python import_to_db.py
    python import_to_db.py --db morpheme_dicts.db
    python import_to_db.py --dry-run
    python import_to_db.py --replace
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
APP_ASSETS_DIR = PROJECT_ROOT.parent / "water_sorting_word_roots" / "app" / "src" / "main" / "assets"

# Known 2-letter ISO 639-1 codes
KNOWN_CODES = {
    "ar", "zh", "da", "nl", "en", "fi", "fr", "de",
    "it", "ja", "ko", "no", "pl", "pt", "ru", "es",
    "sv", "tr", "sl",
}


def parse_lang_pair_from_stem(stem: str):
    """Extract (target_lang, home_lang) from a filename stem.

    Handles:
      'de-en'               → ('de', 'en')
      'de-en-dictionary-1'  → ('de', 'en')
      'ru-en-1'             → ('ru', 'en')
      'mandarin-1'          → None (skipped)
      'de-news-2025-...'    → None (skipped — 'news' is not a lang code)
    """
    parts = stem.split("-")
    if len(parts) >= 2 and parts[0] in KNOWN_CODES and parts[1] in KNOWN_CODES:
        return parts[0], parts[1]
    return None


def _entry_score(entry: dict) -> tuple:
    """Return (num_parts, total_homeLangDetails_len) for conflict resolution."""
    parts = entry.get("parts", [])
    details_len = sum(len(p.get("homeLangDetails", "") or "") for p in parts)
    return (len(parts), details_len)


def _better_entry(existing: dict, candidate: dict) -> dict:
    """Return whichever entry is 'better' by the conflict resolution rules."""
    es, cs = _entry_score(existing), _entry_score(candidate)
    if cs > es:
        return candidate
    return existing


def import_json_file(
    conn,
    path: Path,
    target_lang: str,
    home_lang: str,
    source_label: str,
    dry_run: bool,
    replace: bool,
    counters: dict,
):
    """Import all entries from a single JSON dict file."""
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        print(f"  ERROR reading {path}: {e}")
        counters["errors"] += 1
        return

    words = data.get("words", [])
    if not words:
        print(f"  {path.name}: empty — skipping")
        return

    inserted = 0
    conflicts = 0
    conflict_log = []

    for entry in words:
        word_id = entry.get("id")
        if not word_id:
            continue

        # Check for existing entry in DB
        existing_row = conn.execute(
            "SELECT word_id FROM entries WHERE target_lang=? AND home_lang=? AND word_id=?",
            (target_lang, home_lang, word_id),
        ).fetchone()

        if existing_row and not replace:
            # Conflict: apply resolution strategy
            from morpheme_db import get_entry, entry_to_dict
            existing_entry = get_entry(conn, target_lang, home_lang, word_id)
            winner = _better_entry(existing_entry, entry)
            if winner is entry:
                # Candidate is better — update
                conflict_log.append(
                    f"    [{word_id}] candidate wins ({_entry_score(entry)} vs {_entry_score(existing_entry)})"
                )
                if not dry_run:
                    from morpheme_db import insert_entry
                    insert_entry(conn, target_lang, home_lang, entry, source=source_label, replace=True)
                inserted += 1
            else:
                conflict_log.append(
                    f"    [{word_id}] existing wins ({_entry_score(existing_entry)} vs {_entry_score(entry)})"
                )
            conflicts += 1
        else:
            if not dry_run:
                from morpheme_db import insert_entry
                insert_entry(conn, target_lang, home_lang, entry, source=source_label, replace=replace)
            inserted += 1

    pair_str = f"[{target_lang}, {home_lang}]"
    msg = f"  {pair_str} {path.name}: {len(words)} entries"
    if conflicts:
        msg += f" ({inserted} inserted/updated, {conflicts} conflicts)"
    else:
        msg += f" ({inserted} inserted)"
    print(msg)
    if conflict_log:
        for line in conflict_log[:10]:
            print(line)
        if len(conflict_log) > 10:
            print(f"    ... and {len(conflict_log) - 10} more conflicts")

    counters["entries"] += inserted
    counters["conflicts"] += conflicts


def import_dicts(conn, dry_run: bool, replace: bool):
    """Import dicts/*.json (excluding flagged files)."""
    print("\n--- dicts/*.json ---")
    counters = {"entries": 0, "conflicts": 0, "errors": 0}
    dicts_dir = PROJECT_ROOT / "dicts"
    paths = sorted(dicts_dir.glob("*.json"))
    for path in paths:
        if "flagged" in path.name:
            continue
        pair = parse_lang_pair_from_stem(path.stem)
        if pair is None:
            print(f"  {path.name}: could not detect lang pair — skipping")
            continue
        target_lang, home_lang = pair
        import_json_file(conn, path, target_lang, home_lang, f"dicts/{path.name}", dry_run, replace, counters)
    print(f"  Total: {counters['entries']} entries, {counters['conflicts']} conflicts, {counters['errors']} errors")
    return counters


def import_app_assets(conn, dry_run: bool, replace: bool):
    """Import app assets JSON files."""
    print("\n--- app assets ---")
    counters = {"entries": 0, "conflicts": 0, "errors": 0}
    if not APP_ASSETS_DIR.exists():
        print(f"  App assets dir not found: {APP_ASSETS_DIR} — skipping")
        return counters
    paths = sorted(APP_ASSETS_DIR.glob("*.json"))
    for path in paths:
        pair = parse_lang_pair_from_stem(path.stem)
        if pair is None:
            print(f"  {path.name}: could not detect lang pair — skipping")
            continue
        target_lang, home_lang = pair
        import_json_file(conn, path, target_lang, home_lang, f"app:{path.name}", dry_run, replace, counters)
    print(f"  Total: {counters['entries']} entries, {counters['conflicts']} conflicts, {counters['errors']} errors")
    return counters


def import_discrepancies(conn, dry_run: bool):
    """Import review/discrepancies.json into known_discrepancies."""
    print("\n--- review/discrepancies.json → known_discrepancies ---")
    path = PROJECT_ROOT / "review" / "discrepancies.json"
    if not path.exists():
        print("  Not found — skipping")
        return
    try:
        items = json.loads(path.read_text())
    except Exception as e:
        print(f"  ERROR: {e}")
        return
    if not isinstance(items, list):
        items = items.get("discrepancies", [])
    count = 0
    for item in items:
        word_id = item.get("word") or item.get("id") or item.get("word_id", "")
        if not word_id:
            continue
        if not dry_run:
            conn.execute(
                """INSERT OR REPLACE INTO known_discrepancies
                   (target_lang, home_lang, word_id, category, field, issue, correction, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.get("target_lang", ""),
                    item.get("home_lang", ""),
                    word_id,
                    item.get("category"),
                    item.get("field"),
                    item.get("issue"),
                    item.get("correction"),
                    item.get("status", "confirmed"),
                ),
            )
        count += 1
    if not dry_run:
        conn.commit()
    print(f"  {count} discrepancies imported")


def import_flagged_files(conn, dry_run: bool):
    """Import review/flagged-*.json into verification_flags."""
    print("\n--- review/flagged-*.json → verification_flags ---")
    review_dir = PROJECT_ROOT / "review"
    if not review_dir.exists():
        print("  review/ dir not found — skipping")
        return
    paths = sorted(review_dir.glob("flagged-*.json"))
    if not paths:
        print("  No flagged files found")
        return
    total = 0
    for path in paths:
        # Derive lang pair from the stem after "flagged-"
        # e.g. flagged-en-tr.json → stem "en-tr"
        inner_stem = path.stem[len("flagged-"):]
        pair = parse_lang_pair_from_stem(inner_stem)
        try:
            flags = json.loads(path.read_text())
        except Exception as e:
            print(f"  ERROR reading {path.name}: {e}")
            continue
        if not isinstance(flags, list):
            flags = flags.get("flags", [])
        count = 0
        for flag in flags:
            word_id = flag.get("word") or flag.get("id") or ""
            if not word_id:
                continue
            # Determine lang pair from the flag itself or from the filename
            if pair:
                tl, hl = pair
            else:
                src = flag.get("source_file", "")
                src_pair = parse_lang_pair_from_stem(Path(src).stem) if src else None
                if src_pair:
                    tl, hl = src_pair
                else:
                    tl, hl = "", ""
            if not dry_run:
                conn.execute(
                    """INSERT INTO verification_flags
                       (target_lang, home_lang, word_id, category, field, issue, suggestion)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tl, hl, word_id,
                        flag.get("category"),
                        flag.get("field"),
                        flag.get("issue"),
                        flag.get("suggestion"),
                    ),
                )
            count += 1
        if not dry_run:
            conn.commit()
        print(f"  {path.name}: {count} flags")
        total += count
    print(f"  Total: {total} flags")


def import_glossaries(conn, dry_run: bool):
    """Import prompts/*/glossary.txt into morphemes."""
    print("\n--- prompts/*/glossary.txt → morphemes ---")
    prompts_dir = PROJECT_ROOT / "prompts"
    total = 0
    for pair_dir in sorted(prompts_dir.iterdir()):
        if not pair_dir.is_dir():
            continue
        glossary = pair_dir / "glossary.txt"
        if not glossary.exists():
            continue
        pair = parse_lang_pair_from_stem(pair_dir.name)
        if pair is None:
            print(f"  {pair_dir.name}/glossary.txt: could not detect lang pair — skipping")
            continue
        target_lang, home_lang = pair
        count = 0
        for line in glossary.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2:
                continue
            morpheme, short_gloss = parts[0], parts[1]
            details = parts[2] if len(parts) >= 3 else None
            if not dry_run:
                conn.execute(
                    """INSERT OR REPLACE INTO morphemes
                       (target_lang, home_lang, morpheme, short_gloss, home_lang_details)
                       VALUES (?, ?, ?, ?, ?)""",
                    (target_lang, home_lang, morpheme, short_gloss, details),
                )
            count += 1
        if not dry_run:
            conn.commit()
        print(f"  {pair_dir.name}/glossary.txt: {count} morphemes")
        total += count
    print(f"  Total: {total} morphemes")


def import_wordlists(conn, dry_run: bool):
    """Import word-lists/*.txt into wordlist_words, marking done if already in entries."""
    print("\n--- word-lists/*.txt → wordlist_words ---")
    wl_dir = PROJECT_ROOT / "word-lists"
    if not wl_dir.exists():
        print("  word-lists/ dir not found — skipping")
        return
    total_added = 0
    total_done = 0
    for path in sorted(wl_dir.glob("*.txt")):
        # Infer lang pair from filename stem (e.g. 'de-en-words' → ('de', 'en'))
        stem = path.stem
        if stem.endswith("-words"):
            stem = stem[: -len("-words")]
        pair = parse_lang_pair_from_stem(stem)
        if pair is None:
            print(f"  {path.name}: could not detect lang pair — skipping")
            continue
        target_lang, home_lang = pair

        words = [
            line.strip()
            for line in path.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        added = 0
        already_done = 0
        for word in words:
            # Check if already in entries
            in_entries = conn.execute(
                "SELECT 1 FROM entries WHERE target_lang=? AND home_lang=? AND LOWER(word_id)=LOWER(?)",
                (target_lang, home_lang, word),
            ).fetchone()
            status = "done" if in_entries else "pending"
            if in_entries:
                already_done += 1
            if not dry_run:
                conn.execute(
                    """INSERT OR IGNORE INTO wordlist_words
                       (target_lang, home_lang, word, status, source_file)
                       VALUES (?, ?, ?, ?, ?)""",
                    (target_lang, home_lang, word, status, path.name),
                )
            added += 1
        if not dry_run:
            conn.commit()
        print(f"  {path.name}: {added} words ({already_done} already done)")
        total_added += added
        total_done += already_done
    print(f"  Total: {total_added} words, {total_done} already done")


def count_entries(conn) -> int:
    row = conn.execute("SELECT COUNT(*) as n FROM entries").fetchone()
    return row["n"]


def main():
    parser = argparse.ArgumentParser(
        description="Import all existing data into the morpheme dictionaries SQLite DB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to DB file (default: morpheme_dicts.db in project root)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be imported without writing anything",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Overwrite existing DB entries on conflict (default: keep existing)",
    )
    args = parser.parse_args()

    from morpheme_db import get_db
    conn = get_db(args.db)

    if args.dry_run:
        print("DRY RUN — no changes will be written\n")

    before = count_entries(conn)

    import_dicts(conn, dry_run=args.dry_run, replace=args.replace)
    import_app_assets(conn, dry_run=args.dry_run, replace=args.replace)
    import_discrepancies(conn, dry_run=args.dry_run)
    import_flagged_files(conn, dry_run=args.dry_run)
    import_glossaries(conn, dry_run=args.dry_run)
    import_wordlists(conn, dry_run=args.dry_run)

    after = count_entries(conn)
    print(f"\n=== Import complete ===")
    print(f"Entries before: {before}")
    print(f"Entries after:  {after}")
    print(f"Net new:        {after - before}")

    conn.close()


if __name__ == "__main__":
    main()
