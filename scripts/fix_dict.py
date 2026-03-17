#!/usr/bin/env python3
"""
Fix flagged morpheme dictionary entries using a Haiku pass.

Reads flagged entries from the DB (or from a JSON flags file for backward compat),
sends each flagged entry to Claude with the specific issues found, and writes
corrections back to the DB.

Usage:
    # DB mode (default):
    python fix_dict.py --target-lang de --home-lang en
    python fix_dict.py --target-lang en --home-lang ja --home Japanese

    # JSON file mode (backward compat):
    python fix_dict.py --input dicts/en-ja.json --flags review/flagged-en-ja.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

import anthropic

from cost_tracker import CostTracker

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_BATCH_SIZE = 5
RESOLVABLE_CATEGORIES = {"wrong_gloss", "wrong_article", "wrong_translation", "wrong_morpheme_type"}

LANG_FROM_CODE = {
    "ar": "Arabic", "zh": "Chinese", "da": "Danish", "nl": "Dutch",
    "en": "English", "fi": "Finnish", "fr": "French", "de": "German",
    "it": "Italian", "ja": "Japanese", "ko": "Korean", "no": "Norwegian",
    "pl": "Polish", "pt": "Portuguese", "ru": "Russian", "es": "Spanish",
    "sv": "Swedish", "tr": "Turkish", "sl": "Slovenian", "ga": "Irish Gaelic"
}


def infer_home_lang(input_path: Path) -> str:
    """Infer home language from filename like en-ja.json -> Japanese."""
    stem = input_path.stem
    parts = stem.split("-")
    if len(parts) == 2:
        code = parts[1]
        return LANG_FROM_CODE.get(code, code)
    return "Unknown"


def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        print(f"Error: Prompt file not found: {path}")
        sys.exit(1)
    return path.read_text().strip()


def parse_response(text: str) -> list:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text.rsplit("\n```", 1)[0].strip()
    return json.loads(text).get("words", [])


def merge_fix(original: dict, fix: dict) -> dict:
    """Apply a fix response onto an original entry."""
    merged = dict(original)
    for field in ("translationShort", "translationLong", "exampleTranslation"):
        if field in fix:
            merged[field] = fix[field]
    if "parts" in fix:
        merged_parts = []
        for i, orig_part in enumerate(original.get("parts", [])):
            part = dict(orig_part)
            if i < len(fix["parts"]):
                fix_part = fix["parts"][i]
                if "homeLang" in fix_part:
                    part["homeLang"] = fix_part["homeLang"]
                if "homeLangDetails" in fix_part:
                    part["homeLangDetails"] = fix_part["homeLangDetails"]
            merged_parts.append(part)
        merged["parts"] = merged_parts
    merged.pop("flag", None)
    return merged


def run_fixes(
    flagged_ids: list,
    entries_by_id: dict,
    flags_by_id: dict,
    flag_ids_by_word: dict,
    home_lang_name: str,
    client: anthropic.Anthropic,
    model: str,
    batch_size: int,
    tracker: "CostTracker",
    conn=None,
    target_lang: str = "",
    home_lang: str = "",
) -> int:
    """Run the fix API pass. Writes fixes to DB per-batch. Returns count of fixed entries."""
    from morpheme_db import update_entry, resolve_flag
    system_prompt = load_prompt("fix.txt").replace("<HOMELANG>", home_lang_name)
    batches = [
        flagged_ids[i : i + batch_size]
        for i in range(0, len(flagged_ids), batch_size)
    ]
    total_fixed = 0

    for batch_num, batch_ids in enumerate(batches, 1):
        batch_payload = [
            {"entry": entries_by_id[wid], "flags": flags_by_id[wid]}
            for wid in batch_ids
        ]
        user_message = (
            f"Fix these flagged entries for {home_lang_name} speakers:\n\n"
            + json.dumps({"words": batch_payload}, ensure_ascii=False, indent=2)
        )
        print(f"Batch {batch_num}/{len(batches)} ({len(batch_ids)} entries)...", end=" ", flush=True)

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": user_message}],
                )
                tracker.add(response.usage)
                fixes = parse_response(response.content[0].text)
                batch_fixed = 0
                for fix in fixes:
                    wid = fix.get("id")
                    if wid and wid in entries_by_id:
                        fixed_entry = merge_fix(entries_by_id[wid], fix)
                        if conn and target_lang and home_lang:
                            update_entry(conn, target_lang, home_lang, wid, fixed_entry)
                            for flag in flags_by_id.get(wid, []):
                                if flag.get("category") in RESOLVABLE_CATEGORIES:
                                    resolve_flag(conn, flag["id"], status="fixed", resolved_by=model)
                        batch_fixed += 1
                        total_fixed += 1
                print(f"done ({batch_fixed} fixed, {total_fixed} total)")
                break

            except json.JSONDecodeError as e:
                if attempt < max_attempts:
                    print(f"PARSE ERROR (attempt {attempt}/{max_attempts}), retrying... {e}")
                    time.sleep(1)
                else:
                    print(f"PARSE ERROR after {max_attempts} attempts, skipping batch: {e}")

            except anthropic.APIError as e:
                print(f"API ERROR: {e}")
                sys.exit(1)

        if batch_num < len(batches):
            time.sleep(0.5)

    return total_fixed


def main():
    parser = argparse.ArgumentParser(
        description="Fix flagged morpheme dictionary entries using Claude",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--target-lang", metavar="CODE",
        help="Target language ISO code (DB mode; use with --home-lang)",
    )
    source_group.add_argument(
        "--input",
        help="Dict JSON file to fix (JSON backward-compat mode)",
    )
    parser.add_argument(
        "--home-lang", metavar="CODE",
        help="Home language ISO code (required with --target-lang)",
    )
    parser.add_argument(
        "--home", default=None,
        help="Home language name for prompt (inferred from code if omitted)",
    )
    parser.add_argument(
        "--flags",
        help="Flags JSON file (JSON mode only)",
    )
    parser.add_argument("--api-key", default=None, help="Anthropic API key")
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"Entries per API call (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Claude model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument("--db", default=None, help="Path to DB file")
    args = parser.parse_args()

    if args.target_lang and not args.home_lang:
        parser.error("--home-lang is required when --target-lang is given")

    client = anthropic.Anthropic(api_key=args.api_key)

    if args.target_lang:
        # DB mode
        from morpheme_db import get_db, get_entries, get_open_flags, update_entry, resolve_flag

        target_lang = args.target_lang
        home_lang = args.home_lang
        home_lang_name = args.home or LANG_FROM_CODE.get(home_lang, home_lang)
        print(f"Home language: {home_lang_name}")

        conn = get_db(args.db)
        entries = get_entries(conn, target_lang, home_lang, all_entries=True)
        if not entries:
            print(f"No entries found in DB for [{target_lang}-{home_lang}]")
            conn.close()
            return

        open_flags = get_open_flags(conn, target_lang, home_lang)
        if not open_flags:
            print(f"No open flags in DB for [{target_lang}-{home_lang}]. Nothing to fix.")
            conn.close()
            return

        entries_by_id = {e["id"]: e for e in entries}
        flags_by_id: dict = {}
        flag_ids_by_word: dict = {}
        for flag in open_flags:
            wid = flag["word_id"]
            flags_by_id.setdefault(wid, []).append(flag)
            flag_ids_by_word.setdefault(wid, []).append(flag["id"])

        flagged_ids = [wid for wid in flags_by_id if wid in entries_by_id]
        if not flagged_ids:
            print("No flagged entries found in DB entries. Nothing to fix.")
            conn.close()
            return
        print(f"Found {len(flagged_ids)} entries with open flags.")

        pair = f"{target_lang}-{home_lang}"
        tracker = CostTracker(script="fix_dict", pair=pair, model=args.model)
        try:
            total_fixed = run_fixes(
                flagged_ids, entries_by_id, flags_by_id, flag_ids_by_word,
                home_lang_name, client, args.model, args.batch_size, tracker,
                conn=conn, target_lang=target_lang, home_lang=home_lang,
            )
            print(f"\nDone. {total_fixed} entries fixed in DB for [{target_lang}-{home_lang}]")
        finally:
            tracker.finish()
            conn.close()

    else:
        # JSON file mode (backward compat)
        input_path = Path(args.input)
        if not args.flags:
            parser.error("--flags is required in JSON mode")
        flags_path = Path(args.flags)

        if not input_path.exists():
            print(f"Error: Dict file not found: {args.input}")
            sys.exit(1)
        if not flags_path.exists():
            print(f"Error: Flags file not found: {args.flags}")
            sys.exit(1)

        home_lang_name = args.home or infer_home_lang(input_path)
        print(f"Home language: {home_lang_name}")

        dict_data = json.loads(input_path.read_text())
        entries_by_id = {e["id"]: e for e in dict_data.get("words", [])}

        flags_data = json.loads(flags_path.read_text())
        all_flags = flags_data if isinstance(flags_data, list) else flags_data.get("flags", [])

        flags_by_id: dict = {}
        for flag in all_flags:
            word_id = flag.get("word") or flag.get("id")
            if not word_id:
                continue
            flags_by_id.setdefault(word_id, []).append(flag)

        flagged_ids = [wid for wid in flags_by_id if wid in entries_by_id]
        if not flagged_ids:
            print("No flagged entries found in dict. Nothing to fix.")
            return
        print(f"Found {len(flagged_ids)} flagged entries to fix.")

        pair = input_path.stem
        tracker = CostTracker(script="fix_dict", pair=pair, model=args.model)
        fixed = run_fixes(flagged_ids, entries_by_id, flags_by_id, home_lang_name, client, args.model, args.batch_size, tracker)

        for wid, fixed_entry in fixed.items():
            entries_by_id[wid] = fixed_entry

        dict_data["words"] = [entries_by_id[e["id"]] for e in dict_data["words"]]
        input_path.write_text(json.dumps(dict_data, ensure_ascii=False, indent=2) + "\n")
        tracker.finish()
        print(f"\nDone. {len(fixed)} entries fixed. Dict saved to {args.input}")


if __name__ == "__main__":
    main()
