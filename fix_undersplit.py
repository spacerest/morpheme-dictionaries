#!/usr/bin/env python3
"""
Auto-fix entries where a compound part can be expanded into sub-parts
based on the self-referential undersplit check.

For each entry where a part exactly matches another word with ≥2 real parts:
- Replaces that part with the sub-parts from the matched word
- Filters out placeholder parts (targetLang='0')
- Adjusts all part indices and updates part_count

Fugen-suffix cases (e.g. 'geburtstags' matching 'geburtstag') are flagged
for manual review rather than auto-fixed, since the suffix handling is
ambiguous (it may already be a separate part, or need to become one).

Usage:
    python fix_undersplit.py --pair de-en --dry-run   # preview only
    python fix_undersplit.py --pair de-en              # apply fixes
    python fix_undersplit.py                           # all pairs
"""

import argparse
from morpheme_db import get_db, split_pair, get_all_pairs

_FUGEN = ("en", "es", "er", "ns", "s", "n", "e")


def get_word_parts(conn, target_lang, home_lang, word_id):
    """Return real (non-placeholder) parts of a word ordered by part_index."""
    rows = conn.execute(
        """SELECT target_lang_text, home_lang_text, home_lang_details
           FROM parts WHERE target_lang=? AND home_lang=? AND word_id=?
           ORDER BY part_index""",
        (target_lang, home_lang, word_id),
    ).fetchall()
    # Filter out placeholder parts
    return [r for r in rows if r["target_lang_text"] != "0"]


def get_all_word_part_counts(conn, target_lang, home_lang):
    """Return {lowercased_word_id: real_part_count} for all entries in pair."""
    rows = conn.execute(
        """SELECT LOWER(e.word_id), COUNT(p.part_index)
           FROM entries e
           JOIN parts p USING (target_lang, home_lang, word_id)
           WHERE e.target_lang=? AND e.home_lang=? AND p.target_lang_text != '0'
           GROUP BY e.word_id""",
        (target_lang, home_lang),
    ).fetchall()
    return dict(rows)


def get_all_entry_parts(conn, target_lang, home_lang):
    """Return {word_id: [part_rows]} for all entries."""
    rows = conn.execute(
        """SELECT e.word_id, p.part_index, p.target_lang_text,
                  p.home_lang_text, p.home_lang_details
           FROM entries e
           JOIN parts p USING (target_lang, home_lang, word_id)
           WHERE e.target_lang=? AND e.home_lang=?
           ORDER BY e.rowid, p.part_index""",
        (target_lang, home_lang),
    ).fetchall()
    result = {}
    for r in rows:
        result.setdefault(r["word_id"], []).append(r)
    return result


def find_fugen_match(text, word_part_counts):
    """Return matched word_id if text matches via Fugen suffix stripping, else None."""
    base = text.strip("-").lower()
    for suffix in _FUGEN:
        if base.endswith(suffix) and len(base) - len(suffix) >= 4:
            candidate = base[: -len(suffix)]
            if candidate in word_part_counts and word_part_counts[candidate] >= 2:
                return candidate
    return None


def fix_pair(conn, target_lang, home_lang, dry_run=False):
    pair = f"{target_lang}-{home_lang}"
    word_part_counts = get_all_word_part_counts(conn, target_lang, home_lang)
    all_parts = get_all_entry_parts(conn, target_lang, home_lang)

    auto_fixed = []
    flagged = []

    for word_id, parts in all_parts.items():
        new_parts = []
        changed = False
        needs_flag = []

        for part in parts:
            tl = part["target_lang_text"]
            stripped = tl.strip("-").lower()

            if len(stripped) < 4 or tl == "0":
                new_parts.append(part)
                continue

            # Exact match
            if stripped in word_part_counts and word_part_counts[stripped] >= 2:
                sub = get_word_parts(conn, target_lang, home_lang, stripped)
                if sub and len(sub) >= 2:
                    new_parts.extend(sub)
                    changed = True
                    continue

            # Fugen match — flag for manual review
            fugen_match = find_fugen_match(tl, word_part_counts)
            if fugen_match:
                needs_flag.append(f"part '{tl}' → Fugen match '{fugen_match}' ({word_part_counts[fugen_match]} parts)")
                new_parts.append(part)
                continue

            new_parts.append(part)

        if needs_flag:
            flagged.append({"word_id": word_id, "notes": needs_flag})

        if changed:
            auto_fixed.append(word_id)
            if not dry_run:
                # Delete old parts and re-insert expanded ones
                conn.execute(
                    "DELETE FROM parts WHERE target_lang=? AND home_lang=? AND word_id=?",
                    (target_lang, home_lang, word_id),
                )
                for i, p in enumerate(new_parts):
                    conn.execute(
                        """INSERT INTO parts
                               (target_lang, home_lang, word_id, part_index,
                                target_lang_text, home_lang_text, home_lang_details)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            target_lang, home_lang, word_id, i,
                            p["target_lang_text"], p["home_lang_text"],
                            p["home_lang_details"] if "home_lang_details" in p.keys() else None,
                        ),
                    )
                conn.execute(
                    "UPDATE entries SET part_count=?, updated_at=datetime('now') WHERE target_lang=? AND home_lang=? AND word_id=?",
                    (len(new_parts), target_lang, home_lang, word_id),
                )

    if not dry_run and auto_fixed:
        conn.commit()

    action = "Would fix" if dry_run else "Fixed"
    print(f"[{pair}] {action} {len(auto_fixed)} entries, {len(flagged)} need manual review")

    if auto_fixed:
        print(f"  Auto-fixed:")
        for w in auto_fixed:
            print(f"    {w}")

    if flagged:
        print(f"  Manual review needed (Fugen suffix cases):")
        for f in flagged:
            for note in f["notes"]:
                print(f"    {f['word_id']}: {note}")

    return len(auto_fixed), len(flagged)


def main():
    parser = argparse.ArgumentParser(description="Fix undersplit morpheme entries")
    parser.add_argument("--pair", metavar="XX-YY", help="Single lang pair (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    conn = get_db(args.db)

    pairs = [split_pair(args.pair)] if args.pair else get_all_pairs(conn)

    total_fixed = total_flagged = 0
    for target_lang, home_lang in pairs:
        fixed, flagged = fix_pair(conn, target_lang, home_lang, dry_run=args.dry_run)
        total_fixed += fixed
        total_flagged += flagged

    print(f"\n=== Summary ===")
    action = "Would fix" if args.dry_run else "Fixed"
    print(f"{action}: {total_fixed} entries")
    print(f"Manual review needed: {total_flagged} entries")

    conn.close()


if __name__ == "__main__":
    main()
