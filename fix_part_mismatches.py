#!/usr/bin/env python3
"""
Fix the 5 entries that couldn't be auto-propagated due to part count mismatches.

Each fix is hand-coded since we know the exact structural change needed.
Where a new part is inserted, homeLang uses a universal label ("-" for
connectors, "(noun suffix)" for nominal -y, etc.) or inherits the
existing part's homeLang.

Run from project root:
    python fix_part_mismatches.py --dry-run
    python fix_part_mismatches.py
"""

import argparse
import json
from pathlib import Path


def fix_chronology(parts: list) -> list | None:
    """chron + logy  →  chron + o + logy"""
    if len(parts) == 3:
        return None  # already fixed (en-ref)
    if len(parts) == 2 and parts[0].get("targetLang") == "chron" and parts[1].get("targetLang") == "logy":
        return [
            parts[0],
            {"targetLang": "o", "homeLang": "-"},
            parts[1],
        ]
    return None  # unexpected structure, skip


def fix_geography(parts: list) -> list | None:
    """geo + graph  →  geo + graph + y"""
    if len(parts) == 3:
        return None  # already fixed
    if len(parts) == 2 and parts[0].get("targetLang") == "geo" and parts[1].get("targetLang") == "graph":
        return [
            parts[0],
            parts[1],
            {"targetLang": "y", "homeLang": "(noun suffix)"},
        ]
    return None


def fix_reorganization(parts: list) -> list | None:
    """re + organize + tion  →  re + organ + iz + ation"""
    if len(parts) == 4:
        return None  # already fixed
    if (
        len(parts) == 3
        and parts[0].get("targetLang") == "re"
        and parts[1].get("targetLang") == "organize"
        and parts[2].get("targetLang") in ("tion", "ation")
    ):
        return [
            parts[0],  # re — keep existing homeLang
            {"targetLang": "organ", "homeLang": parts[1].get("homeLang", "organ")},
            {"targetLang": "iz", "homeLang": "(-ize)"},
            {"targetLang": "ation", "homeLang": parts[2].get("homeLang", "-ation")},
        ]
    return None


def fix_investigator(parts: list) -> list | None:
    """investigate + or  →  invest + igat + or"""
    if len(parts) == 3:
        return None  # already fixed
    if (
        len(parts) == 2
        and parts[0].get("targetLang") == "investigate"
        and parts[1].get("targetLang") == "or"
    ):
        return [
            {"targetLang": "invest", "homeLang": parts[0].get("homeLang", "track/trace")},
            {"targetLang": "igat", "homeLang": "-"},
            parts[1],  # or — keep existing homeLang
        ]
    return None


def fix_misinterpret(parts: list) -> list | None:
    """mis + inter + pret + et  →  mis + inter + pret"""
    if len(parts) == 3:
        return None  # already fixed
    if (
        len(parts) == 4
        and parts[0].get("targetLang") == "mis"
        and parts[1].get("targetLang") == "inter"
        and parts[2].get("targetLang") == "pret"
        and parts[3].get("targetLang") == "et"
    ):
        return parts[:3]
    return None


FIXERS = {
    "chronology": fix_chronology,
    "geography": fix_geography,
    "reorganization": fix_reorganization,
    "investigator": fix_investigator,
    "misinterpret": fix_misinterpret,
}


def process_file(path: Path, dry_run: bool) -> int:
    data = json.loads(path.read_text())
    fixed = 0
    for word in data.get("words", []):
        wid = word.get("id", "")
        if wid not in FIXERS:
            continue
        new_parts = FIXERS[wid](word.get("parts", []))
        if new_parts is None:
            continue
        if dry_run:
            old_tls = [p.get("targetLang") for p in word["parts"]]
            new_tls = [p.get("targetLang") for p in new_parts]
            print(f"  [{wid}] {old_tls} → {new_tls}")
        word["parts"] = new_parts
        fixed += 1

    if fixed and not dry_run:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    return fixed


def main():
    parser = argparse.ArgumentParser(description="Fix part-count mismatch entries in en-XX dicts")
    parser.add_argument("files", nargs="*", help="Dict files (default: all dicts/en-*.json)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.files:
        paths = [Path(f) for f in args.files]
    else:
        paths = sorted(
            p for p in Path("dicts").glob("en-*.json")
            if "flagged" not in p.name
        )

    if args.dry_run:
        print("DRY RUN\n")

    total = 0
    for path in paths:
        n = process_file(path, dry_run=args.dry_run)
        if n:
            print(f"{path.name}: fixed {n} entries")
            total += n

    print(f"\nTotal: {total} entries fixed across {len(paths)} files")


if __name__ == "__main__":
    main()
