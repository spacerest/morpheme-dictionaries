#!/usr/bin/env python3
"""
Re-verify specific failed batches from a verify run.

Extracts the entries for each failed batch, runs verify on just those entries,
and merges any new flags into the existing flagged files in review/.

Usage:
    python retry_failed_batches.py --api-key "$MORPHEME_SORT_ANTHROPIC_API_KEY"
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
BATCH_SIZE = 5

# Failed batches from the verify run: (dict_file, batch_number_1indexed)
FAILED_BATCHES = [
    ("dicts/en-sv.json", 5),
    ("dicts/en-tr.json", 3),
]


def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        print(f"Error: Prompt file not found: {path}")
        sys.exit(1)
    return path.read_text().strip()


def parse_flags(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text.rsplit("\n```", 1)[0].strip()
    return json.loads(text).get("flags", [])


def main():
    parser = argparse.ArgumentParser(description="Re-verify specific failed batches")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    verify_prompt = load_prompt("verify.txt")
    client = anthropic.Anthropic(api_key=args.api_key)
    REVIEW_DIR.mkdir(exist_ok=True)

    for dict_file, batch_num in FAILED_BATCHES:
        dict_path = Path(dict_file)
        if not dict_path.exists():
            print(f"Skipping {dict_file} (not found)")
            continue

        entries = json.loads(dict_path.read_text()).get("words", [])
        start = (batch_num - 1) * BATCH_SIZE
        end = start + BATCH_SIZE
        batch = entries[start:end]

        if not batch:
            print(f"Skipping {dict_file} batch {batch_num} (out of range)")
            continue

        ids = [e["id"] for e in batch]
        print(f"{dict_file} batch {batch_num} ({len(batch)} entries: {', '.join(ids)})...", end=" ", flush=True)

        user_message = (
            "Please verify these dictionary entries:\n\n"
            + json.dumps({"words": batch}, ensure_ascii=False, indent=2)
        )

        max_attempts = 3
        new_flags = []
        for attempt in range(1, max_attempts + 1):
            try:
                response = client.messages.create(
                    model=args.model,
                    max_tokens=2048,
                    system=verify_prompt,
                    messages=[{"role": "user", "content": user_message}],
                )
                new_flags = parse_flags(response.content[0].text)
                for flag in new_flags:
                    flag["source_file"] = dict_path.name
                print(f"done ({len(new_flags)} flags)")
                break

            except json.JSONDecodeError as e:
                if attempt < max_attempts:
                    print(f"PARSE ERROR (attempt {attempt}/{max_attempts}), retrying...")
                    time.sleep(1)
                else:
                    print(f"PARSE ERROR after {max_attempts} attempts, skipping.")

            except anthropic.RateLimitError:
                wait = 20 * attempt
                print(f"RATE LIMIT (attempt {attempt}/{max_attempts}), waiting {wait}s...")
                time.sleep(wait)
                if attempt == max_attempts:
                    print("Rate limit persists, skipping.")

            except anthropic.APIError as e:
                print(f"API ERROR: {e}")
                sys.exit(1)

        # Merge new flags into the existing flagged file for this dict
        if new_flags:
            flagged_path = REVIEW_DIR / f"flagged-{dict_path.stem}.json"
            existing = []
            if flagged_path.exists():
                existing = json.loads(flagged_path.read_text())
                if isinstance(existing, dict):
                    existing = existing.get("flags", [])
            # Avoid duplicates by word+field
            existing_keys = {(f.get("word"), f.get("field")) for f in existing}
            added = [f for f in new_flags if (f.get("word"), f.get("field")) not in existing_keys]
            merged = existing + added
            flagged_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
            print(f"  -> {len(added)} new flags merged into {flagged_path.name}")

        time.sleep(0.5)

    print("\nDone.")


if __name__ == "__main__":
    main()
