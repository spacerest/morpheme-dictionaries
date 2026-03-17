#!/usr/bin/env python3
"""
Re-gloss morpheme dictionary entries for a different home language.

Takes an existing dictionary (generated with one home language) and translates
all home-language-facing fields into a new home language, while keeping all
target-language content (morpheme spellings, example sentences, etc.) unchanged.

Fields translated: parts[].homeLang, parts[].homeLangDetails, translationShort,
translationLong, exampleTranslation.

The source pair is read from the DB; the output pair is written to the DB.

Usage:
    # DB mode (default):
    python regloss_dict.py \\
        --source-pair en-en --target-pair en-de \\
        --source-home English --home German

    # JSON file mode (backward compat):
    python regloss_dict.py --input dicts/en-en-ref.json \\
        --output dicts/en-de.json --source-home English --home German
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Support project-specific key name as fallback
if not os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("MORPHEME_SORT_ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = os.environ["MORPHEME_SORT_ANTHROPIC_API_KEY"]

import anthropic

from cost_tracker import CostTracker

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
DEFAULT_BATCH_SIZE = 10
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def repair_json_quotes(s: str) -> str:
    """Escape unescaped double-quote characters inside JSON string values."""
    result = []
    i = 0
    in_string = False
    while i < len(s):
        c = s[i]
        if c == "\\" and in_string and i + 1 < len(s):
            result.append(c)
            result.append(s[i + 1])
            i += 2
            continue
        if c == '"':
            if not in_string:
                in_string = True
                result.append(c)
            else:
                j = i + 1
                while j < len(s) and s[j] in " \t\r\n":
                    j += 1
                if j >= len(s) or s[j] in ":,}]":
                    in_string = False
                    result.append(c)
                else:
                    result.append('\\"')
        else:
            result.append(c)
        i += 1
    return "".join(result)


def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        print(f"Error: Prompt file not found: {path}")
        sys.exit(1)
    return path.read_text().strip()


def merge_entries(source_entries: list, translated_entries: list) -> list:
    """Merge translated home-language fields back into source entries."""
    translated_entries = [e for e in translated_entries if isinstance(e, dict) and "id" in e]
    translated_by_id = {e["id"]: e for e in translated_entries}
    result = []
    for i, source in enumerate(source_entries):
        entry_id = source["id"]
        trans = translated_by_id.get(entry_id)
        if trans is None and i < len(translated_entries):
            # Positional fallback — model may have changed the id (e.g. romaji for kanji)
            trans = translated_entries[i]
            print(f"  Note: id mismatch for '{entry_id}', matched positionally to '{trans.get('id')}'")
        if trans is None:
            print(f"  Warning: no translation returned for '{entry_id}', keeping source")
            result.append(source)
            continue
        merged = dict(source)
        for field in ("translationShort", "translationLong", "exampleTranslation"):
            if field in trans:
                merged[field] = trans[field]
        if "parts" in trans:
            merged_parts = []
            for i, source_part in enumerate(source.get("parts", [])):
                part = dict(source_part)
                if i < len(trans["parts"]):
                    trans_part = trans["parts"][i]
                    if "homeLang" in trans_part:
                        part["homeLang"] = trans_part["homeLang"]
                    if "homeLangDetails" in trans_part:
                        part["homeLangDetails"] = trans_part["homeLangDetails"]
                    elif "homeLangDetails" in part:
                        pass  # Keep source details if translation omitted them
                merged_parts.append(part)
            merged["parts"] = merged_parts
        result.append(merged)
    return result


# Tool definition — forces structured output, eliminates JSON parse errors
REGLOSS_TOOL = {
    "name": "regloss_entries",
    "description": "Return the re-glossed dictionary entries",
    "input_schema": {
        "type": "object",
        "properties": {
            "words": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "translationShort": {"type": "string"},
                        "translationLong": {"type": "string"},
                        "exampleTranslation": {"type": "string"},
                        "parts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "homeLang": {"type": "string"},
                                    "homeLangDetails": {"type": "string"},
                                },
                            },
                        },
                    },
                    "required": ["id", "parts"],
                },
            }
        },
        "required": ["words"],
    },
}


def run_regloss(
    source_entries: list,
    done_ids: set,
    source_home: str,
    target_home: str,
    client: anthropic.Anthropic,
    model: str,
    batch_size: int,
    system_prompt: str,
    tracker: "CostTracker",
) -> list:
    """Run regloss API pass, returning merged entry list."""
    remaining = [e for e in source_entries if e["id"].lower() not in done_ids]
    if not remaining:
        print("All entries already processed.")
        return []

    batches = [remaining[i : i + batch_size] for i in range(0, len(remaining), batch_size)]
    done_count = 0
    all_results = []

    for batch_num, batch in enumerate(batches, 1):
        user_message = (
            f"Re-gloss these entries from {source_home} to {target_home}:\n\n"
            + json.dumps({"words": batch}, ensure_ascii=False, indent=2)
        )
        print(f"Batch {batch_num}/{len(batches)} ({len(batch)} entries)...", end=" ", flush=True)

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    tools=[REGLOSS_TOOL],
                    tool_choice={"type": "tool", "name": "regloss_entries"},
                    system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": user_message}],
                )
                tracker.add(response.usage)
                cache_read = getattr(response.usage, "cache_read_input_tokens", 0)
                cache_status = f" [cache {'hit' if cache_read else 'miss'}]"
                tool_block = next(b for b in response.content if b.type == "tool_use")
                raw_input = tool_block.input
                translated = raw_input.get("words", []) if isinstance(raw_input, dict) else []
                if isinstance(translated, str):
                    try:
                        translated = json.loads(translated)
                    except json.JSONDecodeError:
                        try:
                            translated = json.loads(repair_json_quotes(translated))
                        except json.JSONDecodeError:
                            translated = []
                print(f"  DEBUG: translated count={len(translated)}, sample={str(translated[:1])[:200]}")
                merged = merge_entries(batch, translated)
                all_results.extend(merged)
                done_count += len(merged)
                print(f"done ({done_count}/{len(remaining)}){cache_status}")
                break

            except anthropic.RateLimitError:
                wait = 20 * attempt
                print(f"RATE LIMIT (attempt {attempt}/{max_attempts}), waiting {wait}s...")
                time.sleep(wait)
                if attempt == max_attempts:
                    print("Rate limit persists, giving up on batch.")

            except anthropic.APIError as e:
                print(f"API ERROR: {e}")
                sys.exit(1)

        if batch_num < len(batches):
            time.sleep(0.3)

    return all_results


def main():
    parser = argparse.ArgumentParser(
        description="Re-gloss morpheme dictionary entries for a different home language",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--source-pair",
        metavar="XX-YY",
        help="Source lang pair in DB (e.g. 'en-en')",
    )
    source_group.add_argument(
        "--input",
        help="Source dictionary JSON file (backward-compat mode)",
    )
    parser.add_argument(
        "--word-set", metavar="NAME", default=None,
        help="Only verify entries tagged with this word_set value (e.g. 'first_release_dictionary')",
    )
    parser.add_argument(
        "--target-pair",
        metavar="XX-YY",
        help="Output lang pair for DB mode (e.g. 'en-de'; required with --source-pair)",
    )
    parser.add_argument(
        "--output",
        help="Output JSON file (JSON mode only)",
    )
    parser.add_argument("--home", required=True, help="Target home language, e.g. 'German'")
    parser.add_argument(
        "--source-home", required=True,
        help="Home language of the source, e.g. 'English'",
    )
    parser.add_argument(
        "--api-key", default=None,
        help="Anthropic API key (or set ANTHROPIC_API_KEY)",
    )
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

    if args.source_pair and not args.target_pair:
        parser.error("--target-pair is required when --source-pair is given")
    if args.input and not args.output:
        parser.error("--output is required in JSON mode")

    system_prompt = (
        load_prompt("regloss.txt")
        .replace("<HOMELANG>", args.home)
        .replace("<SOURCEHOMELANG>", args.source_home)
    )
    client = anthropic.Anthropic(api_key=args.api_key)

    if args.source_pair:
        # DB mode
        from morpheme_db import get_db, split_pair, get_entries, get_done_ids, insert_entry

        source_target, source_home_code = split_pair(args.source_pair)
        out_target, out_home_code = split_pair(args.target_pair)

        conn = get_db(args.db)
        source_entries = get_entries(conn, source_target, source_home_code, word_set=args.word_set, all_entries=not args.word_set)
        if not source_entries:
            print(f"No entries found in DB for source pair [{args.source_pair}]")
            conn.close()
            sys.exit(1)
        print(f"Loaded {len(source_entries)} entries from DB [{args.source_pair}]")

        done_ids = get_done_ids(conn, out_target, out_home_code)
        if done_ids:
            print(f"Resuming: {len(done_ids)} already done")

        tracker = CostTracker(script="regloss_dict", pair=args.target_pair, model=args.model)
        results = run_regloss(
            source_entries, done_ids, args.source_home, args.home,
            client, args.model, args.batch_size, system_prompt, tracker,
        )
        for entry in results:
            insert_entry(conn, out_target, out_home_code, entry, source=f"regloss:{args.source_pair}")

        total = len(get_done_ids(conn, out_target, out_home_code))
        print(f"\nDone. {total} total entries in DB for [{args.target_pair}]")
        tracker.finish()
        conn.close()

    else:
        # JSON file mode (backward compat)
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"Error: Input file not found: {args.input}")
            sys.exit(1)

        source_data = json.loads(input_path.read_text())
        source_entries = source_data.get("words", [])
        print(f"Loaded {len(source_entries)} entries from {args.input}")

        output_path = Path(args.output)
        output_data: dict = {"words": []}
        if output_path.exists():
            try:
                output_data = json.loads(output_path.read_text())
            except Exception:
                pass
        done_ids = {entry["id"].lower() for entry in output_data["words"]}
        if done_ids:
            print(f"Resuming: {len(done_ids)} already done")

        tracker = CostTracker(script="regloss_dict", pair=Path(args.output).stem, model=args.model)
        results = run_regloss(
            source_entries, done_ids, args.source_home, args.home,
            client, args.model, args.batch_size, system_prompt, tracker,
        )
        output_data["words"].extend(results)
        output_path.write_text(json.dumps(output_data, ensure_ascii=False, indent=2) + "\n")
        tracker.finish()
        print(f"\nDone. {len(output_data['words'])} total entries written to {args.output}")


if __name__ == "__main__":
    main()
