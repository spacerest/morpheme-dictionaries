#!/usr/bin/env python3
"""
Filter a raw German word list down to base forms suitable for the morpheme dictionary.

Keeps:
  - Singular nouns (lemmatized)
  - Verb infinitives (lemmatized)
  - Base-form adjectives (lemmatized)

Excludes:
  - Proper nouns, unknown tokens, numbers
  - Plurals, conjugated verbs, inflected adjectives
  - Words over --max-len characters (long compounds won't fit the game)
  - Duplicates (deduped by lemma)

Usage:
    python filter_words.py --input word-lists/de-news-2025-no-punc.txt \
                           --output word-lists/de-news-filtered.txt
"""

import argparse
from pathlib import Path
import spacy

KEEP_POS = {"NOUN", "VERB", "ADJ"}

# Suffixes to strip when checking for near-duplicate stems, ordered longest-first
# so we don't accidentally strip a short suffix that's part of a longer one
STEM_SUFFIXES = ["ungen", "nen", "en", "es", "er", "em", "e", "n", "s"]


def rough_stem(word: str) -> str:
    """Strip one common German inflection suffix to get a rough stem."""
    for suffix in STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def is_base_form(token) -> bool:
    """Return True if the token is in its base/dictionary form."""
    morph = token.morph

    if token.pos_ == "NOUN":
        number = morph.get("Number")
        return not number or number == ["Sing"]

    if token.pos_ == "VERB":
        verb_form = morph.get("VerbForm")
        return not verb_form or verb_form == ["Inf"]

    if token.pos_ == "ADJ":
        # Keep base (positive) degree, no case inflection
        degree = morph.get("Degree")
        return not degree or degree == ["Pos"]

    return False


def main():
    parser = argparse.ArgumentParser(description="Filter German word list to base forms")
    parser.add_argument("--input", required=True, help="Input word list (one word per line)")
    parser.add_argument("--output", required=True, help="Output word list")
    parser.add_argument("--min-len", type=int, default=12,
                        help="Skip words shorter than this (default: 12)")
    parser.add_argument("--max-len", type=int, default=25,
                        help="Skip words longer than this (default: 25)")
    args = parser.parse_args()

    print("Loading spaCy German model...")
    nlp = spacy.load("de_core_news_md", disable=["parser", "ner"])

    words = [
        line.strip().lower()
        for line in Path(args.input).read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    print(f"Read {len(words)} words")

    # Length filter first (fast, no NLP needed)
    words = [w for w in words if (len(w) <= args.max_len) and (len(w) >= args.min_len)]
    print(f"After length filter (min {args.min_len} and max {args.max_len}): {len(words)} words")

    # Run through spaCy in batches
    seen_lemmas = set()
    kept = []
    skipped_pos = 0
    skipped_form = 0
    skipped_dupe = 0

    batch_size = 1000
    for i in range(0, len(words), batch_size):
        batch = words[i : i + batch_size]
        docs = list(nlp.pipe(batch))
        for word, doc in zip(batch, docs):
            if not doc:
                continue
            token = doc[0]

            if token.pos_ not in KEEP_POS:
                skipped_pos += 1
                continue

            if not is_base_form(token):
                skipped_form += 1
                continue

            lemma = token.lemma_.lower()
            if lemma in seen_lemmas:
                skipped_dupe += 1
                continue

            seen_lemmas.add(lemma)
            kept.append(lemma)

        if (i // batch_size + 1) % 5 == 0:
            print(f"  Processed {min(i + batch_size, len(words))}/{len(words)}...")

    # Second pass: deduplicate by rough stem
    # Group words by stem, keep only the shortest in each group
    stem_to_words: dict[str, list[str]] = {}
    for word in kept:
        stem = rough_stem(word)
        stem_to_words.setdefault(stem, []).append(word)

    final = sorted(min(group, key=len) for group in stem_to_words.values())
    skipped_stem = len(kept) - len(final)

    Path(args.output).write_text("\n".join(final) + "\n")

    print(f"\nKept:                {len(final)}")
    print(f"Skipped (POS):       {skipped_pos}")
    print(f"Skipped (form):      {skipped_form}")
    print(f"Skipped (dupe):      {skipped_dupe}")
    print(f"Skipped (stem dedup): {skipped_stem}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
