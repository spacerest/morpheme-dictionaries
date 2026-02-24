#!/usr/bin/env python3
"""
Generate morpheme dictionary entries using the Claude API.

Usage:
    python generate_claude.py --input words.txt --output dict.json \
        --home English --target German --api-key sk-ant-...

    # Or set ANTHROPIC_API_KEY env var and omit --api-key:
    python generate_claude.py --input words.txt --output dict.json \
        --home English --target German

Saves progress after each batch, so interrupted runs can be resumed
by re-running the same command.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import anthropic

PROMPTS_DIR = Path(__file__).parent / "prompts"
DEFAULT_BATCH_SIZE = 25
DEFAULT_MODEL = "claude-sonnet-4-6"


def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        print(f"Error: Prompt file not found: {path}")
        sys.exit(1)
    return path.read_text().strip()


def fill_template(template: str, home: str, target: str, words: str = "") -> str:
    return (
        template
        .replace("<HOMELANG>", home)
        .replace("<TARGETLANG>", target)
        .replace("<WORDS>", words)
    )


def load_existing(output_path: Path) -> dict:
    """Load an existing output file so interrupted runs can be resumed."""
    if output_path.exists():
        try:
            return json.loads(output_path.read_text())
        except Exception:
            pass
    return {"words": []}


def save_output(output_path: Path, data: dict):
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def parse_response(text: str) -> list[dict]:
    """Parse the JSON response, stripping markdown fences if Claude added them."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text.rsplit("\n```", 1)[0].strip()
    return json.loads(text).get("words", [])


def main():
    parser = argparse.ArgumentParser(
        description="Generate morpheme dictionary entries using Claude",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", required=True, help="Word list file (one word per line)")
    parser.add_argument("--output", required=True, help="Output JSON file")
    parser.add_argument("--home", required=True, help="Home language, e.g. 'English'")
    parser.add_argument("--target", required=True, help="Target language, e.g. 'German'")
    parser.add_argument(
        "--api-key",
        default=None,
        help="Anthropic API key (falls back to ANTHROPIC_API_KEY env var if omitted)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Words per API call (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Claude model to use (default: {DEFAULT_MODEL})",
    )
    args = parser.parse_args()

    # Load prompts and fill in language names
    system = fill_template(load_prompt("system.txt"), args.home, args.target)
    user_template = load_prompt("user.txt")

    # Load word list
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {args.input}")
        sys.exit(1)

    words = [
        line.strip()
        for line in input_path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    print(f"Loaded {len(words)} words from {args.input}")

    # Resume: skip words already present in the output file
    output_path = Path(args.output)
    output_data = load_existing(output_path)
    done_ids = {entry["id"] for entry in output_data["words"]}
    remaining = [w for w in words if w.lower() not in done_ids]

    if done_ids:
        print(f"Resuming: {len(done_ids)} already done, {len(remaining)} remaining")
    if not remaining:
        print("All words already processed.")
        return

    # Batch up the remaining words
    batches = [
        remaining[i : i + args.batch_size]
        for i in range(0, len(remaining), args.batch_size)
    ]

    # api_key=None makes anthropic fall back to ANTHROPIC_API_KEY env var
    client = anthropic.Anthropic(api_key=args.api_key)
    done_count = 0

    for batch_num, batch in enumerate(batches, 1):
        user_message = fill_template(user_template, args.home, args.target, "\n".join(batch))
        print(f"Batch {batch_num}/{len(batches)} ({len(batch)} words)...", end=" ", flush=True)

        try:
            response = client.messages.create(
                model=args.model,
                max_tokens=8192,
                system=system,
                messages=[{"role": "user", "content": user_message}],
            )
            entries = parse_response(response.content[0].text)
            output_data["words"].extend(entries)
            save_output(output_path, output_data)
            done_count += len(entries)
            print(f"done ({done_count}/{len(remaining)})")

        except json.JSONDecodeError as e:
            print(f"PARSE ERROR: {e}")
            print(f"  Response was: {response.content[0].text[:300]}...")
            print("  Batch skipped — re-run to retry.")

        except anthropic.APIError as e:
            print(f"API ERROR: {e}")
            sys.exit(1)

        if batch_num < len(batches):
            time.sleep(0.5)

    print(f"\nDone. {len(output_data['words'])} total entries written to {args.output}")

    flagged = [e for e in output_data["words"] if "flag" in e]
    if flagged:
        print(f"\n{len(flagged)} flagged entries (review manually):")
        for e in flagged:
            print(f"  {e['id']}: {e['flag']}")


if __name__ == "__main__":
    main()
