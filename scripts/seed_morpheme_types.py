#!/usr/bin/env python3
"""
Seed parts.morpheme_type from the morphemes glossary table.

For each morpheme entry, derive its type from dash notation:
  be-        → prefix  (trailing dash, no leading dash)
  -ing       → suffix  (leading dash, no trailing dash)
  -ver-      → infix   (both)
  root / ع-ل-م → root (no edge dashes, or internal-only dashes)

Only updates parts where morpheme_type IS NULL.

Handles multi-variant morphemes (e.g. "-tion/-sion") by splitting on "/" and
requiring all variants to agree on a type; skips if they don't.

Usage:
    python seed_morpheme_types.py [--db PATH] [--dry-run]
"""

import argparse
import re
from morpheme_db import get_db


def morpheme_type_from_notation(morpheme: str, short_gloss: str = "") -> str | None:
    """Return morpheme type inferred from dash notation, or None if ambiguous.

    For morphemes with dashes on both sides (e.g. -s-, -fahr-):
    - If the gloss mentions 'linking' or 'connecting' → 'linking'
    - Otherwise → 'root' (bound root, not a true infix)
    True infixes (inserted within a root) are rare enough to be set manually.
    """
    # Split multi-variant forms like "-tion/-sion" or "im-/in-/il-"
    variants = [v.strip() for v in morpheme.split("/") if v.strip()]
    types = set()
    for v in variants:
        has_leading = v.startswith("-")
        has_trailing = v.endswith("-")
        if has_leading and has_trailing:
            gloss_lower = short_gloss.lower()
            if "linking" in gloss_lower or "connecting" in gloss_lower or "connective" in gloss_lower:
                types.add("linking")
            else:
                types.add("root")  # bound root, not a true infix
        elif has_leading:
            types.add("suffix")
        elif has_trailing:
            types.add("prefix")
        else:
            types.add("root")
    if len(types) == 1:
        return types.pop()
    # Mixed types across variants — skip
    return None


def bare_forms(morpheme: str) -> list[str]:
    """Return all bare (dash-stripped) surface forms for a morpheme entry."""
    variants = [v.strip() for v in morpheme.split("/") if v.strip()]
    return list({v.strip("-") for v in variants if v.strip("-")})


def main():
    parser = argparse.ArgumentParser(description="Seed morpheme types from glossary")
    parser.add_argument("--db", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_db(args.db)

    morphemes = conn.execute(
        "SELECT target_lang, home_lang, morpheme, short_gloss FROM morphemes"
    ).fetchall()

    print(f"Processing {len(morphemes)} morpheme entries...")

    updated = 0
    skipped_ambiguous = 0
    skipped_empty = 0

    for row in morphemes:
        target_lang, home_lang, morpheme, short_gloss = row[0], row[1], row[2], row[3] or ""
        mt = morpheme_type_from_notation(morpheme, short_gloss)
        if mt is None:
            skipped_ambiguous += 1
            continue

        forms = bare_forms(morpheme)
        if not forms:
            skipped_empty += 1
            continue

        for form in forms:
            if not form:
                continue
            if args.dry_run:
                count = conn.execute(
                    """SELECT COUNT(*) FROM parts
                       WHERE target_lang=? AND home_lang=? AND target_lang_text=?
                         AND morpheme_type IS NULL""",
                    (target_lang, home_lang, form),
                ).fetchone()[0]
                if count:
                    print(f"  [{target_lang}-{home_lang}] '{form}' ({morpheme}) → {mt}  ({count} parts)")
                    updated += count
            else:
                result = conn.execute(
                    """UPDATE parts SET morpheme_type=?
                       WHERE target_lang=? AND home_lang=? AND target_lang_text=?
                         AND morpheme_type IS NULL""",
                    (mt, target_lang, home_lang, form),
                )
                updated += result.rowcount

    if not args.dry_run:
        conn.commit()

    print(f"\nUpdated:           {updated} parts")
    print(f"Skipped (ambiguous type): {skipped_ambiguous}")
    print(f"Skipped (empty bare form): {skipped_empty}")

    # Summary of what got filled
    if not args.dry_run:
        dist = conn.execute(
            "SELECT morpheme_type, COUNT(*) FROM parts GROUP BY morpheme_type ORDER BY 2 DESC"
        ).fetchall()
        print("\nmorpheme_type distribution after seed:")
        for r in dist:
            print(f"  {r[0] or 'NULL':12s}  {r[1]}")

    conn.close()


if __name__ == "__main__":
    main()
