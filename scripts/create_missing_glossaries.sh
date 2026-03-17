#!/usr/bin/env bash
# Generate glossaries for all pairs that have only a placeholder or no glossary.
# Run from project root: bash scripts/create_missing_glossaries.sh
# Runs up to MAX_JOBS concurrently.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MAX_JOBS=4

PAIRS=(
  # XX-en pairs with placeholder-only glossary files
  ar-en
  da-en
  es-en
  it-en
  pt-en
  ru-en
  sv-en
  zh-en
  # en-XX pair with no glossary at all
  en-sl
)

for pair in "${PAIRS[@]}"; do
  echo "[$pair] Starting..."
  python "$SCRIPT_DIR/create_glossary.py" --pair "$pair" --count 25 &

  # Throttle to MAX_JOBS parallel jobs
  while [ "$(jobs -r | wc -l)" -ge "$MAX_JOBS" ]; do
    sleep 1
  done
done

wait
echo ""
echo "All done!"
