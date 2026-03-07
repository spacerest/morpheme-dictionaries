#!/usr/bin/env python3
"""
Verify morpheme dictionary entries using a second Claude pass.

Checks for frozen compounds, false cognates, wrong boundaries, wrong articles,
bad translations, and unnatural example sentences. Inserts flags into the DB
for human review. Confirmed issues can be promoted to known_discrepancies and
will be fed back into future generation runs.

Saves a checkpoint after each batch so interrupted runs can be resumed
by re-running the same command.

Usage:
    # DB mode (default):
    python verify_dict.py --target-lang de --home-lang en
    python verify_dict.py --target-lang de --home-lang en --output review/flagged-de-en.json

    # JSON file mode (backward compat):
    python verify_dict.py --input dicts/de-en.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

import anthropic

PROMPTS_DIR = Path(__file__).parent / "prompts"
REVIEW_DIR = Path(__file__).parent / "review"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 5  # Small batches for careful verification


def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        print(f"Error: Prompt file not found: {path}")
        sys.exit(1)
    return path.read_text().strip()


def parse_flags(text: str) -> list:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text.rsplit("\n```", 1)[0].strip()
    return json.loads(text).get("flags", [])


def checkpoint_path(label: str) -> Path:
    safe = label.replace("/", "_").replace("\\", "_")
    return REVIEW_DIR / f".checkpoint-{safe}.json"


def load_checkpoint(label: str) -> tuple:
    """Return (verified_ids, flags_so_far) from checkpoint, or empty if none."""
    cp = checkpoint_path(label)
    if cp.exists():
        try:
            data = json.loads(cp.read_text())
            return set(data.get("verified_ids", [])), data.get("flags", [])
        except Exception:
            pass
    return set(), []


def save_checkpoint(label: str, verified_ids: set, flags: list):
    cp = checkpoint_path(label)
    cp.write_text(
        json.dumps({"verified_ids": list(verified_ids), "flags": flags}, ensure_ascii=False, indent=2) + "\n"
    )


def clear_checkpoint(label: str):
    cp = checkpoint_path(label)
    if cp.exists():
        cp.unlink()


def verify_entries(
    entries: list,
    label: str,
    client: anthropic.Anthropic,
    model: str,
    verify_prompt: str,
) -> list:
    """Verify a list of entry dicts, returning flag dicts."""
    verified_ids, all_flags = load_checkpoint(label)
    if verified_ids:
        print(f"  Resuming: {len(verified_ids)} entries already verified.")

    remaining = [e for e in entries if e["id"] not in verified_ids]
    print(f"  Verifying {len(remaining)}/{len(entries)} entries...")

    batches = [remaining[i : i + BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
    total_batches = len(batches)

    for batch_num, batch in enumerate(batches, 1):
        user_message = (
            "Please verify these dictionary entries:\n\n"
            + json.dumps({"words": batch}, ensure_ascii=False, indent=2)
        )
        print(f"  Batch {batch_num}/{total_batches}...", end=" ", flush=True)

        max_attempts = 3
        success = False
        for attempt in range(1, max_attempts + 1):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=2048,
                    system=verify_prompt,
                    messages=[{"role": "user", "content": user_message}],
                )
                flags = parse_flags(response.content[0].text)
                all_flags.extend(flags)
                for entry in batch:
                    verified_ids.add(entry["id"])
                save_checkpoint(label, verified_ids, all_flags)
                print(f"done ({len(flags)} flags)")
                success = True
                break

            except json.JSONDecodeError as e:
                if attempt < max_attempts:
                    print(f"PARSE ERROR (attempt {attempt}/{max_attempts}), retrying... {e}")
                    time.sleep(1)
                else:
                    print(f"PARSE ERROR after {max_attempts} attempts, skipping batch: {e}")
                    print(f"  Response: {response.content[0].text[:200]}...")

            except anthropic.RateLimitError:
                wait = 20 * attempt
                print(f"RATE LIMIT (attempt {attempt}/{max_attempts}), waiting {wait}s...")
                time.sleep(wait)
                if attempt == max_attempts:
                    print("Rate limit persists, giving up on batch.")

            except anthropic.APIError as e:
                print(f"API ERROR: {e}")
                sys.exit(1)

        if not success:
            for entry in batch:
                verified_ids.add(entry["id"])
            save_checkpoint(label, verified_ids, all_flags)

        if batch_num < total_batches:
            time.sleep(0.3)

    clear_checkpoint(label)
    return all_flags


def main():
    parser = argparse.ArgumentParser(description="Verify morpheme dictionary entries")

    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--target-lang", metavar="CODE",
        help="Target language ISO code (reads from DB; use with --home-lang)",
    )
    source_group.add_argument(
        "--input", nargs="+",
        help="Dict JSON file(s) to verify (backward-compat mode)",
    )

    parser.add_argument(
        "--home-lang", metavar="CODE",
        help="Home language ISO code (required with --target-lang)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Also write flags to this JSON file (for human review)",
    )
    parser.add_argument(
        "--api-key", default=None, help="Anthropic API key (or set ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Claude model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--db", default=None, help="Path to DB file",
    )
    parser.add_argument(
        "--to-verify", action="store_true",
        help="Only verify entries with to_verify=1 (ignores import flag)",
    )
    args = parser.parse_args()

    if args.target_lang and not args.home_lang:
        parser.error("--home-lang is required when --target-lang is given")

    verify_prompt = load_prompt("verify.txt")
    client = anthropic.Anthropic(api_key=args.api_key)
    REVIEW_DIR.mkdir(exist_ok=True)

    all_flags = []

    if args.target_lang:
        # DB mode
        from morpheme_db import get_db, get_entries, insert_flag
        conn = get_db(args.db)
        target_lang = args.target_lang
        home_lang = args.home_lang
        label = f"{target_lang}-{home_lang}"
        entries = get_entries(conn, target_lang, home_lang, to_verify=args.to_verify)
        if not entries:
            filter_desc = "to_verify=1" if args.to_verify else "import=1"
            print(f"No entries found in DB for [{target_lang}-{home_lang}] with {filter_desc}")
            conn.close()
            sys.exit(1)
        print(f"Verifying [{target_lang}-{home_lang}]: {len(entries)} entries")
        flags = verify_entries(entries, label, client, args.model, verify_prompt)
        all_flags.extend(flags)

        # Insert flags into DB
        for flag in flags:
            word_id = flag.get("word") or flag.get("id") or ""
            if word_id:
                insert_flag(conn, target_lang, home_lang, word_id, flag)
        print(f"  {len(flags)} flags inserted into DB")

        if args.output is None:
            out_path = REVIEW_DIR / f"flagged-{label}.json"
            out_path.write_text(json.dumps(flags, ensure_ascii=False, indent=2) + "\n")
            print(f"  Flags also written to {out_path}")

        conn.close()

    else:
        # JSON file mode (backward compat)
        for input_str in args.input:
            input_path = Path(input_str)
            if not input_path.exists():
                print(f"Warning: File not found: {input_path}, skipping")
                continue
            try:
                data = json.loads(input_path.read_text())
            except Exception as e:
                print(f"ERROR reading {input_path}: {e}")
                continue
            entries = data.get("words", [])
            if not entries:
                print(f"  No entries found in {input_path.name}")
                continue
            print(f"Verifying {input_path.name}: {len(entries)} entries")
            flags = verify_entries(entries, input_path.stem, client, args.model, verify_prompt)
            for flag in flags:
                flag["source_file"] = input_path.name
            all_flags.extend(flags)

            if len(args.input) > 1 or args.output is None:
                out_path = REVIEW_DIR / f"flagged-{input_path.stem}.json"
                out_path.write_text(json.dumps(flags, ensure_ascii=False, indent=2) + "\n")
                print(f"  Flags written to {out_path}")

    if args.output:
        Path(args.output).write_text(json.dumps(all_flags, ensure_ascii=False, indent=2) + "\n")

    print(f"\nTotal flags: {len(all_flags)}")
    if all_flags:
        from collections import Counter
        cats = Counter(f["category"] for f in all_flags if "category" in f)
        for cat, count in cats.most_common():
            print(f"  {cat}: {count}")
        print(f"\nReview flagged entries in the DB (or flagged-*.json files).")
        print(f"Promote confirmed issues to known_discrepancies to avoid repeats.")


if __name__ == "__main__":
    main()
