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

from cost_tracker import CostTracker

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
REVIEW_DIR = Path(__file__).parent.parent / "review"
DEFAULT_MODEL = "claude-sonnet-4-6"
BATCH_SIZE = 3  # Sonnet does more per entry (generates details), keep batches small


def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        print(f"Error: Prompt file not found: {path}")
        sys.exit(1)
    return path.read_text().strip()


def parse_response(text: str) -> tuple:
    """Parse verify response JSON. Returns (flags, types, fixes)."""
    text = text.strip()
    # Normalize fullwidth punctuation used as JSON structural characters
    text = text.replace("\uff0c", ",")   # ， fullwidth comma
    text = text.replace("\uff1a", ":")   # ： fullwidth colon
    # Note: do NOT replace curly quotes or corner brackets here — replacing " / " → "
    # inside string values would create unescaped quotes and break parsing further.
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text.rsplit("\n```", 1)[0].strip()
    # Skip any prose preamble before the JSON object, ignore trailing content
    brace = text.find("{")
    if brace > 0:
        text = text[brace:]
    data, _ = json.JSONDecoder().raw_decode(text)
    return data.get("flags", []), data.get("types", []), data.get("fixes", [])


def _get_field_value(entry: dict, field: str):
    """Look up a field value from an entry dict using verify field path notation."""
    import re
    m = re.match(r"parts\[(\d+)\]\.(\w+)", field)
    if m:
        idx, attr = int(m.group(1)), m.group(2)
        parts = entry.get("parts", [])
        if idx < len(parts):
            camel_to_key = {"homeLang": "homeLang", "homeLangDetails": "homeLangDetails",
                            "targetLang": "targetLang"}
            return parts[idx].get(camel_to_key.get(attr, attr))
        return None
    simple = {"translationShort": "translationShort", "translationLong": "translationLong",
              "exampleTranslation": "exampleTranslation", "article": "article",
              "exampleSentence": "exampleSentence"}
    return entry.get(simple.get(field, field))


def print_batch_diff(batch: list, fixes: list, flags: list):
    """Print before/after for each entry in the batch."""
    fixes_by_word = {}
    for f in fixes:
        fixes_by_word.setdefault(f.get("word", ""), []).append(f)
    flagged_words = {f.get("word", "") for f in flags}

    for entry in batch:
        word_id = entry["id"]
        word_fixes = fixes_by_word.get(word_id, [])
        flagged = word_id in flagged_words
        marker = " [FLAGGED]" if flagged else ""
        if not word_fixes and not flagged:
            print(f"  {word_id}: no changes")
            continue
        print(f"  {word_id}:{marker}")
        for fix in word_fixes:
            field = fix.get("field", "")
            new_val = fix.get("value", "")
            old_val = _get_field_value(entry, field)
            old_str = repr(old_val) if old_val is not None else "—"
            print(f"    {field}: {old_str} → {new_val!r}")


def checkpoint_path(label: str) -> Path:
    safe = label.replace("/", "_").replace("\\", "_")
    return REVIEW_DIR / f".checkpoint-{safe}.json"


def load_checkpoint(label: str) -> tuple:
    """Return (verified_ids, flags_so_far, types_so_far, fixes_so_far) from checkpoint."""
    cp = checkpoint_path(label)
    if cp.exists():
        try:
            data = json.loads(cp.read_text())
            return set(data.get("verified_ids", [])), data.get("flags", []), data.get("types", []), data.get("fixes", [])
        except Exception:
            pass
    return set(), [], [], []


def save_checkpoint(label: str, verified_ids: set, flags: list, types: list, fixes: list):
    cp = checkpoint_path(label)
    cp.write_text(
        json.dumps({"verified_ids": list(verified_ids), "flags": flags, "types": types, "fixes": fixes}, ensure_ascii=False, indent=2) + "\n"
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
    tracker: "CostTracker",
    target_lang: str = "",
    home_lang: str = "",
    glossary: list = None,
    conn=None,
    dry_run: bool = False,
    verbose: bool = False,
) -> list:
    """Verify a list of entry dicts, returning flag dicts.

    If conn + target_lang + home_lang are provided, flags and audit stamps
    are written to the DB after each batch so interruptions don't lose data.
    """
    verified_ids, all_flags, all_types, all_fixes = load_checkpoint(label)
    checkpoint_count = len(verified_ids)
    if checkpoint_count:
        print(f"  Resuming: {checkpoint_count} entries already verified (checkpoint).")

    # Track counts for what's actually written this session (not checkpoint replay)
    session_fixes = 0
    session_types = 0
    session_flags = 0
    session_entries = 0

    remaining = [e for e in entries if e["id"] not in verified_ids]
    print(f"  Verifying {len(remaining)}/{len(entries)} entries...")

    # Build glossary lookup for per-batch filtering
    glossary_by_morpheme = {m["morpheme"]: m["short_gloss"] for m in glossary} if glossary else {}

    batches = [remaining[i : i + BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
    total_batches = len(batches)

    for batch_num, batch in enumerate(batches, 1):
        # Build context header with only glossary entries relevant to this batch
        context_lines = []
        if target_lang and home_lang:
            context_lines.append(f"Target language: {target_lang} | Home language: {home_lang}\n")
        if glossary_by_morpheme:
            batch_morphemes = {p["targetLang"].strip("-") for e in batch for p in e.get("parts", [])}
            relevant = {m: g for m, g in glossary_by_morpheme.items() if m.strip("-") in batch_morphemes}
            if relevant:
                gloss_lines = "\n".join(f"  {m} → {g}" for m, g in relevant.items())
                context_lines.append(f"Canonical glossary for {target_lang}:\n{gloss_lines}\n")
        context_header = "\n".join(context_lines)

        user_message = (
            context_header
            + "Please verify these dictionary entries:\n\n"
            + json.dumps({"words": batch}, ensure_ascii=False, indent=2)
        )
        print(f"  Batch {batch_num}/{total_batches}...", end=" ", flush=True)

        max_attempts = 3
        success = False
        for attempt in range(1, max_attempts + 1):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=8192,
                    system=verify_prompt,
                    messages=[{"role": "user", "content": user_message}],
                )
                tracker.add(response.usage)
                flags, types, fixes = parse_response(response.content[0].text)
                all_flags.extend(flags)
                all_types.extend(types)
                all_fixes.extend(fixes)
                for entry in batch:
                    verified_ids.add(entry["id"])
                save_checkpoint(label, verified_ids, all_flags, all_types, all_fixes)
                print(f"done ({len(flags)} flags, {len(fixes)} fixes, {sum(len(t.get('parts',[])) for t in types)} types)")
                if verbose:
                    print_batch_diff(batch, fixes, flags)
                success = True
                break

            except json.JSONDecodeError as e:
                if attempt < max_attempts:
                    print(f"PARSE ERROR (attempt {attempt}/{max_attempts}), retrying... {e}")
                    time.sleep(1)
                else:
                    print(f"PARSE ERROR after {max_attempts} attempts, skipping batch: {e}")
                    print(f"  Response: {response.content[0].text[:200]}...")
                    debug_path = REVIEW_DIR / "parse_error_debug.txt"
                    debug_path.write_text(response.content[0].text)
                    print(f"  Full response written to {debug_path}")

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
            save_checkpoint(label, verified_ids, all_flags, all_types, all_fixes)

        # Write fixes, flags, types, and audit stamp to DB after each batch
        if conn and target_lang and home_lang and success and not dry_run:
            from morpheme_db import insert_flag, set_morpheme_types, apply_fixes
            batch_ids = [e["id"] for e in batch]
            batch_id_set = set(batch_ids)
            # Apply auto-fixes (also records them in verification_flags as auto_applied)
            batch_fixes = [f for f in fixes if f.get("word") in batch_id_set]
            if batch_fixes:
                apply_fixes(conn, target_lang, home_lang, batch_fixes, model)
            # Insert human-review flags
            for flag in flags:
                word_id = flag.get("word") or flag.get("id") or ""
                if word_id:
                    insert_flag(conn, target_lang, home_lang, word_id, flag)
            # Write morpheme types
            batch_types = [t for t in types if t.get("word") in batch_id_set]
            if batch_types:
                set_morpheme_types(conn, target_lang, home_lang, batch_types)
            conn.execute(
                """UPDATE entries SET to_verify=0, last_audited=datetime('now'), last_auditor=?
                   WHERE target_lang=? AND home_lang=? AND word_id IN ({})""".format(
                    ",".join("?" * len(batch_ids))
                ),
                [model, target_lang, home_lang] + batch_ids,
            )
            conn.commit()
            session_fixes += len(batch_fixes)
            session_types += sum(len(t.get("parts", [])) for t in batch_types)
            session_flags += len(flags)
            session_entries += len(batch_ids)

        if batch_num < total_batches:
            time.sleep(0.3)

    # Catch-up write: apply any checkpoint-replay data not yet written to DB.
    # This handles the case where a previous run completed API calls and saved
    # a checkpoint but crashed before writing to the DB.
    if conn and target_lang and home_lang and not dry_run and verified_ids:
        from morpheme_db import insert_flag, set_morpheme_types, apply_fixes
        all_id_list = list(verified_ids)
        rows = conn.execute(
            "SELECT word_id FROM entries WHERE target_lang=? AND home_lang=? AND word_id IN ({}) AND last_audited IS NULL".format(
                ",".join("?" * len(all_id_list))
            ),
            [target_lang, home_lang] + all_id_list,
        ).fetchall()
        unwritten_ids = {r[0] for r in rows}
        if unwritten_ids:
            unwritten_fixes = [f for f in all_fixes if f.get("word") in unwritten_ids]
            if unwritten_fixes:
                apply_fixes(conn, target_lang, home_lang, unwritten_fixes, model)
            for flag in all_flags:
                word_id = flag.get("word") or flag.get("id") or ""
                if word_id in unwritten_ids:
                    insert_flag(conn, target_lang, home_lang, word_id, flag)
            unwritten_types = [t for t in all_types if t.get("word") in unwritten_ids]
            if unwritten_types:
                set_morpheme_types(conn, target_lang, home_lang, unwritten_types)
            conn.execute(
                """UPDATE entries SET to_verify=0, last_audited=datetime('now'), last_auditor=?
                   WHERE target_lang=? AND home_lang=? AND word_id IN ({})""".format(
                    ",".join("?" * len(all_id_list))
                ),
                [model, target_lang, home_lang] + all_id_list,
            )
            conn.commit()
            catchup_fixes = len(unwritten_fixes)
            catchup_types = sum(len(t.get("parts", [])) for t in unwritten_types)
            session_fixes += catchup_fixes
            session_types += catchup_types
            session_entries += len(unwritten_ids)
            print(f"  (Catch-up: wrote {len(unwritten_ids)} previously-checkpointed entries to DB)")

    clear_checkpoint(label)
    return all_flags, all_types, all_fixes, session_entries, session_fixes, session_types, session_flags


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
    parser.add_argument(
        "--word-set", metavar="NAME", default=None,
        help="Only verify entries tagged with this word_set value (e.g. 'first_release_dictionary')",
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Only process the first N entries (useful for testing)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print proposed fixes without writing anything to the DB",
    )
    parser.add_argument(
        "--rerun", action="store_true",
        help="Include entries that have already been audited (default: skip them)",
    )
    parser.add_argument(
        "--reaudit-after", type=int, default=None, metavar="DAYS",
        help="Include entries last audited more than DAYS days ago (also includes never-audited)",
    )
    parser.add_argument(
        "--word-id", nargs="+", metavar="ID",
        help="Only verify these specific word IDs (overrides all other filters)",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Run a readiness check before processing batches",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Continue even if preflight finds issues",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print before/after for each fix and 'no changes' for unchanged entries",
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
        from morpheme_db import get_db, get_entries, get_morphemes, insert_flag, get_canonical_labels, format_canonical_labels_for_prompt
        conn = get_db(args.db)
        target_lang = args.target_lang
        home_lang = args.home_lang
        canonical_labels = format_canonical_labels_for_prompt(get_canonical_labels(conn, home_lang))
        verify_prompt = verify_prompt.replace("<CANONICAL_LABELS>", canonical_labels)
        label = f"{target_lang}-{home_lang}"
        if args.word_id:
            all_e = get_entries(conn, target_lang, home_lang, all_entries=True)
            by_id = {e["id"]: e for e in all_e}
            entries = [by_id[w] for w in args.word_id if w in by_id]
            missing = [w for w in args.word_id if w not in by_id]
            if missing:
                print(f"Warning: word IDs not found: {missing}")
        else:
            entries = get_entries(conn, target_lang, home_lang, to_verify=args.to_verify, word_set=args.word_set, unaudited_only=not args.rerun and args.reaudit_after is None, max_audit_age_days=args.reaudit_after)
        if args.limit and not args.word_id:
            entries = entries[:args.limit]
        if not entries:
            if args.word_set:
                filter_desc = f"word_set='{args.word_set}'"
            elif args.to_verify:
                filter_desc = "to_verify=1"
            else:
                filter_desc = "all entries"
            print(f"No entries found in DB for [{target_lang}-{home_lang}] with {filter_desc}")
            if not args.to_verify and not args.word_set:
                fallback = get_entries(conn, target_lang, home_lang, to_verify=True)
                if fallback:
                    print(f"  Hint: {len(fallback)} entries exist with to_verify=1 — try adding --to-verify")
            conn.close()
            sys.exit(1)
        glossary = get_morphemes(conn, target_lang, home_lang)
        if glossary:
            print(f"  Loaded {len(glossary)} glossary entries for {target_lang}-{home_lang}")
        dry_label = " (dry run — no DB writes)" if args.dry_run else ""
        print(f"Verifying [{target_lang}-{home_lang}]: {len(entries)} entries{dry_label}")
        tracker = CostTracker(script="verify_dict", pair=label, model=args.model)

        if args.preflight:
            from preflight import run_preflight
            run_preflight(
                client, mode="verify", system_prompt=verify_prompt,
                target_lang=target_lang, home_lang=home_lang,
                glossary_count=len(glossary),
                canonical_labels=canonical_labels,
                sample_items=entries[:3],
                tracker=tracker, force=args.force,
            )

        try:
            flags, types, fixes, session_entries, session_fixes, session_types, session_flags = verify_entries(
                entries, label, client, args.model, verify_prompt, tracker,
                target_lang=target_lang, home_lang=home_lang, glossary=glossary,
                conn=conn, dry_run=args.dry_run, verbose=args.verbose,
            )
        finally:
            tracker.finish()
        all_flags.extend(flags)
        type_count = sum(len(t.get("parts", [])) for t in types)
        if args.dry_run:
            print(f"  {len(flags)} flags, {len(fixes)} proposed fixes, {type_count} morpheme types (NOT written — dry run)")
            if fixes:
                print("\n  Proposed fixes:")
                for fix in fixes:
                    val = fix.get("value", "")
                    val_preview = json.dumps(val, ensure_ascii=False) if isinstance(val, list) else repr(str(val)[:80])
                    print(f"    [{fix.get('word')}] {fix.get('field')} → {val_preview}  ({fix.get('category')})")
            if flags:
                print("\n  Flags for human review:")
                for flag in flags:
                    print(f"    [{flag.get('word')}] {flag.get('category')}: {flag.get('issue','')[:80]}")
        else:
            print(f"  {session_entries} entries written this run: {session_flags} flags, {session_fixes} auto-fixes, {session_types} morpheme types")

        if not args.dry_run:
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
            tracker = CostTracker(script="verify_dict", pair=input_path.stem, model=args.model)
            flags, _types, _fixes, *_ = verify_entries(entries, input_path.stem, client, args.model, verify_prompt, tracker)
            tracker.finish()
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
