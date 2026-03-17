#!/usr/bin/env python3
"""
Preflight readiness check for the morpheme dictionary pipeline.

Sends a single cheap Haiku call to verify that the prompt, glossary,
canonical labels, and sample data look correct before burning API calls
on full batch runs.
"""

import json
import sys

PREFLIGHT_MODEL = "claude-haiku-4-5-20251001"

PREFLIGHT_SYSTEM = (
    "You are a setup validator for a morpheme dictionary pipeline. "
    "Your job is to catch hard blockers — things that will cause the run to fail or produce "
    "garbage output. You are NOT looking for improvements or nice-to-haves. "
    "Only flag issues that would cause clear failure: unfilled template placeholders "
    "(<HOMELANG>, <TARGETLANG>, <CANONICAL_LABELS>, <WORDS> still present as literals), "
    "a language pair mismatch (e.g. sample data is clearly wrong language), "
    "or a completely missing required field. "
    "The system prompt may use examples from other languages — that is normal and expected. "
    "Shared prompts, missing language-specific tuning, and suboptimal setups are NOT blockers. "
    "Respond with exactly READY on its own line if there are no hard blockers. "
    "Otherwise list only genuine blockers, one per line, prefixed with '- '."
)


def run_preflight(
    client,
    mode: str,
    system_prompt: str,
    target_lang: str,
    home_lang: str,
    glossary_count: int,
    canonical_labels: str,
    sample_items: list,
    tracker=None,
    force: bool = False,
) -> bool:
    """Run a preflight check. Returns True if ready, False if issues found.

    If force=False (default), exits on failure. If force=True, prints
    issues but returns False so the caller can decide.
    """
    pair = f"{target_lang}-{home_lang}"

    # Format sample items
    if mode == "generate":
        sample_text = "\n".join(f"  {w}" for w in sample_items[:5])
        sample_label = "words"
    else:
        sample_text = json.dumps(sample_items[:3], ensure_ascii=False, indent=2)
        sample_label = "entries"

    glossary_status = (
        f"{glossary_count} morpheme glossary entries loaded"
        if glossary_count
        else "No glossary loaded"
    )
    labels_status = canonical_labels if canonical_labels else "No canonical labels configured"

    user_message = (
        f"Pipeline preflight check — {mode} mode\n\n"
        f"Language pair: {pair}\n"
        f"Glossary: {glossary_status}\n"
        f"Canonical labels: {labels_status}\n\n"
        f"Full system prompt:\n{system_prompt}\n\n"
        f"Sample {sample_label} ({len(sample_items[:5])} shown):\n{sample_text}\n\n"
        f"Check for hard blockers only:\n"
        f"- Any unfilled placeholders (<HOMELANG>, <TARGETLANG>, <CANONICAL_LABELS>, <WORDS>) still present as literals in the prompt\n"
        f"- Sample data is clearly the wrong language for the pair\n"
        f"- A required field is completely absent (e.g. no system prompt at all)\n\n"
        f"Respond READY if there are no hard blockers. Otherwise list only genuine blockers."
    )

    print(f"  Preflight check ({PREFLIGHT_MODEL})...", end=" ", flush=True)

    try:
        response = client.messages.create(
            model=PREFLIGHT_MODEL,
            max_tokens=512,
            system=PREFLIGHT_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
        )
        if tracker:
            tracker.add(response.usage)
    except Exception as e:
        print(f"PREFLIGHT ERROR: {e}")
        if force:
            print("  --force: continuing despite preflight error.")
            return False
        sys.exit(1)

    text = response.content[0].text.strip()
    first_line = text.split("\n")[0].strip()

    if first_line == "READY":
        print("READY")
        return True

    print("ISSUES FOUND:")
    for line in text.strip().splitlines():
        print(f"    {line}")

    if force:
        print("  --force: continuing despite preflight issues.")
        return False
    else:
        print("  Aborting. Fix the issues above, or pass --force to continue anyway.")
        sys.exit(1)
