#!/usr/bin/env bash
# Run a Sonnet fix pass on all reglossed en-XX dicts using their verify flags.
# Run AFTER verify_all.sh has completed.
# Run from the project root: bash fix_all.sh

set -e

DICTS=(
  dicts/en-ar.json
  dicts/en-da.json
  dicts/en-de.json
  dicts/en-es.json
  dicts/en-fi.json
  dicts/en-fr.json
  dicts/en-it.json
  dicts/en-ja.json
  dicts/en-ko.json
  dicts/en-nl.json
  dicts/en-no.json
  dicts/en-pl.json
  dicts/en-pt.json
  dicts/en-ru.json
  dicts/en-sv.json
  dicts/en-tr.json
  dicts/en-zh.json
)

for dict in "${DICTS[@]}"; do
  stem=$(basename "$dict" .json)        # e.g. en-ja
  flags="review/flagged-${stem}.json"

  if [ ! -f "$dict" ]; then
    echo "=== Skipping $dict (not found) ==="
    continue
  fi
  if [ ! -f "$flags" ]; then
    echo "=== Skipping $dict (no flags file at $flags) ==="
    continue
  fi

  flag_count=$(python3 -c "import json; d=json.load(open('$flags')); print(len(d) if isinstance(d,list) else len(d.get('flags',[])))")
  if [ "$flag_count" -eq 0 ]; then
    echo "=== $dict: no flags, skipping ==="
    continue
  fi

  echo "=== $dict ($flag_count flags) ==="
  python fix_dict.py \
    --input "$dict" \
    --flags "$flags" \
    --api-key "$MORPHEME_SORT_ANTHROPIC_API_KEY"
done

echo ""
echo "All done!"
