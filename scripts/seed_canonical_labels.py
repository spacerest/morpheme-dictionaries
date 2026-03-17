#!/usr/bin/env python3
"""
Seed canonical grammatical label forms into the DB for a given home language.

These labels are injected into generation and verification prompts via the
<CANONICAL_LABELS> placeholder, ensuring Claude uses consistent label forms
(e.g. always "(verb)" never "(inf)" or "(infinitive)").

Usage:
    python seed_canonical_labels.py              # seed English (default)
    python seed_canonical_labels.py --home-lang de
    python seed_canonical_labels.py --home-lang en --db path/to/other.db

For a new home language, either:
  (a) translate the English labels below and add a new LABELS dict, or
  (b) run this script with --home-lang XX and edit the DB manually afterward.

Labels format: (label_type, canonical_form, comma-separated-aliases)
"""

import argparse
import sys

from morpheme_db import get_db, upsert_canonical_label

# ---------------------------------------------------------------------------
# Label definitions per home language
# ---------------------------------------------------------------------------

LABELS = {
    "en": [
        ("verb",               "(verb)",               "(inf),(infinitive),(inf.),(verb.)"),
        ("past_participle",    "(past participle)",    "(pp),(past part.),(pp.),(past part)"),
        ("present_participle", "(present participle)", "(pres part.),(pres),(pres.)"),
        ("plural",             "(plural)",             "(pl.),(pl),(plur),(plur.)"),
        ("genitive",           "(genitive)",           "(gen.),(gen),(genit.)"),
        ("reflexive",          "(reflexive)",          "(refl),(refl.),(reflexiv)"),
        ("perfective",         "(perfective)",         "(perf),(pf),(pf.),(perf.)"),
        ("diminutive",         "(diminutive)",         "(dim.),(dim),(dimin.)"),
    ],
    "de": [
        ("verb",               "(Verb)",       "(Inf.),(inf.),(Infinitiv),(infinitiv)"),
        ("past_participle",    "(Partizip II)", "(Part. II),(Part.II),(PP),(pp)"),
        ("present_participle", "(Partizip I)",  "(Part. I),(Part.I),(PI),(pi)"),
        ("plural",             "(Plural)",      "(Pl.),(pl.),(Plur.),(plur.)"),
        ("genitive",           "(Genitiv)",     "(Gen.),(gen.)"),
        ("reflexive",          "(Reflexiv)",    "(refl.),(Refl.),(reflexiv)"),
        ("perfective",         "(Perfektiv)",   "(perf.),(Perf.),(pf.)"),
        ("diminutive",         "(Diminutiv)",   "(Dim.),(dim.)"),
    ],
}


def main():
    parser = argparse.ArgumentParser(
        description="Seed canonical grammatical label forms into the DB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--home-lang", default="en",
        help="Home language ISO code to seed (default: en)"
    )
    parser.add_argument(
        "--db", default=None,
        help="Path to DB file (default: morpheme_dicts.db in project root)"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Print current labels for the given home language and exit"
    )
    args = parser.parse_args()

    conn = get_db(args.db)

    if args.list:
        from morpheme_db import get_canonical_labels
        rows = get_canonical_labels(conn, args.home_lang)
        if not rows:
            print(f"No canonical labels found for home_lang='{args.home_lang}'")
        else:
            print(f"Canonical labels for '{args.home_lang}':")
            for row in rows:
                print(f"  {row['label_type']:22s} → {row['canonical']:25s}  aliases: {row['aliases'] or ''}")
        conn.close()
        return

    labels = LABELS.get(args.home_lang)
    if not labels:
        print(f"No label definitions found for home_lang='{args.home_lang}'.")
        print(f"Available: {', '.join(LABELS.keys())}")
        print("Add a new entry to LABELS in this script, then re-run.")
        conn.close()
        sys.exit(1)

    for label_type, canonical, aliases in labels:
        upsert_canonical_label(conn, args.home_lang, label_type, canonical, aliases)

    conn.close()
    print(f"Seeded {len(labels)} canonical labels for home_lang='{args.home_lang}'.")


if __name__ == "__main__":
    main()
