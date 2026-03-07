#!/usr/bin/env python3
"""
Export dictionary entries from the SQLite DB to JSON files.

By default exports all lang pairs found in the DB to dicts/.
Use --target-lang/--home-lang to export a single pair.
Use --app to export to the water_sorting_word_roots app assets directory.

Usage:
    python export_to_json.py --all
    python export_to_json.py --target-lang tr --home-lang en
    python export_to_json.py --all --output-dir /tmp/test-export/
    python export_to_json.py --target-lang en --home-lang de --app
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "dicts"
APP_ASSETS_DIR = (
    PROJECT_ROOT.parent
    / "water_sorting_word_roots"
    / "app"
    / "src"
    / "main"
    / "assets"
)


def export_pair(conn, target_lang: str, home_lang: str, output_dir: Path, to_verify: bool = False) -> int:
    """Export one lang pair to {output_dir}/{target_lang}-{home_lang}.json.

    Returns the number of entries written.
    """
    from morpheme_db import get_entries

    entries = get_entries(conn, target_lang, home_lang, to_verify=to_verify)
    if not entries:
        print(f"  [{target_lang}-{home_lang}]: no entries — skipping")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{target_lang}-{home_lang}.json"
    data = {"words": entries}
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"  [{target_lang}-{home_lang}]: {len(entries)} entries → {out_path}")
    return len(entries)


def main():
    parser = argparse.ArgumentParser(
        description="Export morpheme dictionary entries from DB to JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Export all lang pairs (can be combined with --target-lang/--home-lang as filters)",
    )
    parser.add_argument(
        "--target-lang",
        metavar="CODE",
        help="Target language ISO code. With --all, filters to this target lang. Without --all, exports just this pair (requires --home-lang).",
    )
    parser.add_argument(
        "--home-lang",
        metavar="CODE",
        help="Home language ISO code. With --all, filters to this home lang. Without --all, exports just this pair (requires --target-lang).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"Output directory (default: dicts/; use --app for app assets)",
    )
    parser.add_argument(
        "--app",
        action="store_true",
        help="Export to the water_sorting_word_roots app assets directory",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to DB file (default: morpheme_dicts.db in project root)",
    )
    parser.add_argument(
        "--to-verify",
        action="store_true",
        help="Export to_verify=1 entries instead of import=1 entries",
    )
    args = parser.parse_args()

    if not args.all and not (args.target_lang and args.home_lang):
        parser.error("either --all or both --target-lang and --home-lang are required")


    # Resolve output directory
    if args.app:
        output_dir = APP_ASSETS_DIR
    elif args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = DEFAULT_OUTPUT_DIR

    from morpheme_db import get_db, get_all_pairs

    conn = get_db(args.db)

    if args.all:
        pairs = get_all_pairs(conn)

        if args.home_lang:
            pairs = [(tl, hl) for tl, hl in pairs if hl == args.home_lang]
        if args.target_lang:
            pairs = [(tl, hl) for tl, hl in pairs if tl == args.target_lang]
        if not pairs:
            print("No entries found in DB.")
            sys.exit(1)
        print(f"Exporting {len(pairs)} lang pairs to {output_dir}/")
        total = 0
        for target_lang, home_lang in pairs:
            total += export_pair(conn, target_lang, home_lang, output_dir, to_verify=args.to_verify)
        print(f"\nDone. {total} total entries exported.")
    else:
        # Single pair mode
        total = export_pair(conn, args.target_lang, args.home_lang, output_dir, to_verify=args.to_verify)
        if total == 0:
            sys.exit(1)
        print(f"\nDone. {total} entries exported.")

    conn.close()


if __name__ == "__main__":
    main()
