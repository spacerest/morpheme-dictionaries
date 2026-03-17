#!/usr/bin/env python3
"""
Test script: send 5 de-en first_release_dictionary entries to Sonnet
to apply the notation guide. Prints proposed changes — does NOT write to DB.

Usage:
    python test_notation_fix.py
    python test_notation_fix.py --word-ids Begeisterung Benommenheit  # specific entries
    python test_notation_fix.py --commit  # write changes to DB after review
"""

import argparse
import json
import sys
from pathlib import Path

import anthropic

from cost_tracker import CostTracker
from morpheme_db import get_db, get_entries

NOTATION_GUIDE = (Path(__file__).parent.parent / "morpheme-notation.md").read_text()

SYSTEM_PROMPT = f"""You are cleaning up morpheme dictionary entries for a language-learning word puzzle game.
Each part has a `homeLang` tile label the player uses as a building block to puzzle out the word's meaning. Do not pre-solve the puzzle: each tile must reflect only what that morpheme contributes on its own, not the combined meaning of the compound. For example, `druck` in `Ausdruck` means `press`, not `expression` — the player combines `out` + `press` to arrive at `expression` themselves. Other examples of this mistake: `fahr` in `Erfahrung` should be `travel` not `experience`; `stand` in `Verstand` should be `stand` not `understanding`.

Apply the following notation guide to fix the `homeLang` values. Also fix `homeLangDetails` if it:
- Contains em-dashes (replace with commas)
- Opens with "From X..." — this is a hard rule: always write "As in X..." instead. The morpheme is the root; the full verb or noun is built on it, not the other way around.
- Contains slashes listing alternatives (pick the best one; move others to the note)

{NOTATION_GUIDE}

For each entry, return ONLY the parts that need changes. Use this format — no explanation, no markdown:

{{
  "words": [
    {{
      "id": "word_id",
      "parts": [
        {{"index": 0, "homeLang": "new value"}},
        {{"index": 1, "homeLang": "new value", "homeLangDetails": "updated note"}},
        {{"index": 2}}
      ]
    }}
  ]
}}

Include all part indices in the array (use {{}} for parts with no changes). Only include `homeLang` or `homeLangDetails` keys when changing them.
If an entry needs no changes at all, omit it from the output entirely.
"""


def fetch_entries(conn, word_ids=None, limit=5):
    if word_ids:
        entries = []
        all_entries = get_entries(conn, "de", "en", all_entries=True)
        by_id = {e["id"]: e for e in all_entries}
        for wid in word_ids:
            if wid in by_id:
                entries.append(by_id[wid])
            else:
                print(f"Warning: {wid!r} not found in DB")
        return entries
    else:
        all_entries = get_entries(conn, "de", "en", word_set="first_release_dictionary")
        return all_entries[:limit]


def print_diff(entry, proposed_parts):
    orig_parts = {i: p for i, p in enumerate(entry["parts"])}
    prop_by_index = {p["index"]: p for p in proposed_parts if "index" in p}
    has_change = False
    for idx, prop in prop_by_index.items():
        orig = orig_parts.get(idx, {})
        changes = []
        if "homeLang" in prop and prop["homeLang"] != orig.get("homeLang"):
            changes.append(f"  homeLang:        {orig.get('homeLang')!r} → {prop['homeLang']!r}")
        if "homeLangDetails" in prop and prop["homeLangDetails"] != orig.get("homeLangDetails"):
            old = (orig.get("homeLangDetails") or "")[:100]
            new = (prop["homeLangDetails"] or "")[:100]
            changes.append(f"  homeLangDetails: {old!r}…\n                 → {new!r}…")
        if changes:
            if not has_change:
                print(f"\n{'─'*60}")
                print(f"  {entry['id']}  ({entry.get('translationShort','')})")
                has_change = True
            tl = orig.get("targetLang", f"part[{idx}]")
            print(f"  [{idx}] {tl}")
            for c in changes:
                print(c)
    if not has_change:
        print(f"  {entry['id']}: no changes proposed")


def apply_changes(conn, entry, proposed_parts):
    orig_parts = {i: p for i, p in enumerate(entry["parts"])}
    for prop in proposed_parts:
        idx = prop["index"]
        orig = orig_parts.get(idx, {})
        new_hl = prop.get("homeLang", orig.get("homeLang"))
        new_hld = prop.get("homeLangDetails", orig.get("homeLangDetails"))
        conn.execute(
            "UPDATE parts SET home_lang_text=?, home_lang_details=? "
            "WHERE target_lang='de' AND home_lang='en' AND word_id=? AND part_index=?",
            (new_hl, new_hld, entry["id"], idx),
        )
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--word-ids", nargs="+", help="Specific word IDs to test")
    parser.add_argument("--limit", type=int, default=5, help="Number of entries (default 5)")
    parser.add_argument("--commit", action="store_true", help="Write changes to DB")
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    conn = get_db(args.db)
    entries = fetch_entries(conn, word_ids=args.word_ids, limit=args.limit)
    if not entries:
        print("No entries found.")
        sys.exit(1)

    print(f"Testing notation fix on {len(entries)} entries: {[e['id'] for e in entries]}")
    if args.commit:
        print("--commit is set: changes WILL be written to DB after review.")
    else:
        print("Dry run — no DB writes. Pass --commit to apply.")

    client = anthropic.Anthropic()
    tracker = CostTracker(script="test_notation_fix", pair="de-en", model="claude-sonnet-4-6")

    user_msg = "Fix the homeLang notation for these entries:\n\n" + json.dumps(
        {"words": entries}, ensure_ascii=False, indent=2
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        tracker.add(response.usage)
    finally:
        tracker.finish()

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:]).rsplit("```", 1)[0].strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Parse error: {e}\nRaw response:\n{raw}")
        sys.exit(1)

    proposed_by_id = {w["id"]: w.get("parts", []) for w in result.get("words", [])}
    entries_by_id = {e["id"]: e for e in entries}

    print(f"\nProposed changes ({len(proposed_by_id)} entries with changes):")
    for entry in entries:
        proposed = proposed_by_id.get(entry["id"], [])
        print_diff(entry, proposed)

    if args.commit and proposed_by_id:
        print(f"\nWriting {len(proposed_by_id)} entries to DB...")
        for wid, proposed_parts in proposed_by_id.items():
            if wid in entries_by_id:
                apply_changes(conn, entries_by_id[wid], proposed_parts)
        print("Done.")

    conn.close()


if __name__ == "__main__":
    main()
