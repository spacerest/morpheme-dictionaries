#!/usr/bin/env bash
# Run a verify pass on all reglossed en-XX dicts, up to MAX_JOBS concurrently.
# Run from the project root: bash verify_all.sh
#
# Flags are written per-file to review/flagged-en-XX.json

MAX_JOBS=3

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
  dicts/en-sl.json
  dicts/en-sv.json
  dicts/en-tr.json
  dicts/en-zh.json
)

run_verify() {
  local dict="$1"
  if [ ! -f "$dict" ]; then
    echo "[$dict] Skipping (not found)"
    return
  fi
  local stem flags
  stem=$(basename "$dict" .json)
  flags="review/flagged-${stem}.json"
  if [ -f "$flags" ]; then
    echo "[$dict] Already done, skipping"
    return
  fi
  echo "[$dict] Starting..."
  python verify_dict.py \
    --input "$dict" \
    --api-key "$MORPHEME_SORT_ANTHROPIC_API_KEY" \
    2>&1 | sed "s|^|[$dict] |"
  echo "[$dict] Done."
}

export -f run_verify
export MORPHEME_SORT_ANTHROPIC_API_KEY

printf '%s\n' "${DICTS[@]}" | xargs -P "$MAX_JOBS" -I {} bash -c 'run_verify "$@"' _ {}

echo ""
echo "All done! Flagged entries are in review/flagged-en-*.json"
