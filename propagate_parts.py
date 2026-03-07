#!/usr/bin/env python3
"""
Propagate targetLang fixes from en-ref.json into all en-XX language dicts.

en-ref.json holds the canonical English morpheme splits (targetLang).
The en-XX dicts have the same words but with homeLang translated into each
target language. Because regloss doesn't touch targetLang, old incorrect
splits (scrib instead of scrip, vert instead of vers, etc.) remain in all
language dicts even after en-ref was fixed.

What this script does for each word in en-ref:
  - If the en-XX dict has the same word with the same number of parts:
      copy targetLang values part-by-part (homeLang is preserved)
  - If the part count differs (en-ref was re-split):
      skip and log a warning — these need manual fix or re-generation
  - Words in en-XX not in en-ref: leave untouched

Usage:
    python propagate_parts.py                       # all dicts/en-*.json
    python propagate_parts.py dicts/en-de.json      # specific file
    python propagate_parts.py --dry-run             # show changes without writing
"""

import argparse
import json
import sys
from pathlib import Path


def load_ref(ref_path: Path) -> dict[str, list]:
    """Returns {word_id: parts_list} from en-ref."""
    data = json.loads(ref_path.read_text())
    return {w["id"]: w["parts"] for w in data.get("words", [])}


def propagate(dict_path: Path, ref_parts: dict[str, list], dry_run: bool) -> tuple[int, int, int]:
    """
    Returns (updated, skipped_count_mismatch, unchanged).
    """
    data = json.loads(dict_path.read_text())
    words = data.get("words", [])

    updated = 0
    skipped = 0
    unchanged = 0

    for word in words:
        wid = word.get("id", "")
        if wid not in ref_parts:
            unchanged += 1
            continue

        ref = ref_parts[wid]
        current = word.get("parts", [])

        if len(ref) != len(current):
            print(
                f"  [SKIP part-count mismatch] {dict_path.name} [{wid}]: "
                f"en-ref has {len(ref)} parts, dict has {len(current)}"
            )
            skipped += 1
            continue

        # Check if any targetLang actually differs
        any_diff = any(
            ref[i].get("targetLang", "") != current[i].get("targetLang", "")
            for i in range(len(ref))
        )
        if not any_diff:
            unchanged += 1
            continue

        # Apply: copy targetLang from ref, preserve everything else in each part
        for i, part in enumerate(current):
            old_tl = part.get("targetLang", "")
            new_tl = ref[i].get("targetLang", "")
            if old_tl != new_tl and dry_run:
                print(f"  [{wid}] parts[{i}]: {repr(old_tl)} → {repr(new_tl)}")
            part["targetLang"] = new_tl

        updated += 1

    if not dry_run:
        dict_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    return updated, skipped, unchanged


def main():
    parser = argparse.ArgumentParser(description="Propagate targetLang fixes from en-ref to en-XX dicts")
    parser.add_argument("files", nargs="*", help="Dict files (default: all dicts/en-*.json except en-ref)")
    parser.add_argument("--ref", default="dicts/en-ref.json", help="Reference dict (default: dicts/en-ref.json)")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing files")
    args = parser.parse_args()

    ref_path = Path(args.ref)
    if not ref_path.exists():
        print(f"ERROR: ref file not found: {ref_path}")
        sys.exit(1)

    ref_parts = load_ref(ref_path)
    print(f"Loaded {len(ref_parts)} entries from {ref_path.name}")
    if args.dry_run:
        print("DRY RUN — no files will be written\n")

    if args.files:
        paths = [Path(f) for f in args.files]
    else:
        paths = sorted(
            p for p in Path("dicts").glob("en-*.json")
            if p.name not in {"en-ref.json", "en-ref-flagged.json"}
            and "-flagged" not in p.name
        )

    if not paths:
        print("No dict files found.")
        sys.exit(1)

    total_updated = total_skipped = total_unchanged = 0
    for path in paths:
        u, s, n = propagate(path, ref_parts, dry_run=args.dry_run)
        total_updated += u
        total_skipped += s
        total_unchanged += n
        action = "would update" if args.dry_run else "updated"
        print(f"{path.name}: {action} {u} entries, skipped {s} (count mismatch), {n} unchanged")

    print(f"\nTotal: {total_updated} updated, {total_skipped} skipped, {total_unchanged} unchanged")


if __name__ == "__main__":
    main()
