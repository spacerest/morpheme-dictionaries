#!/usr/bin/env bash
# Run a verify pass on all XX-en dicts in the DB, up to MAX_JOBS concurrently.
# Run from the project root: bash scripts/verify_all_xxen.sh
#
# Flags are written to the DB and also exported per-pair to review/flagged-XX-en.json

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MAX_JOBS=3

PAIRS=(
  ar en
  da en
  de en
  es en
  fi en
  fr en
  it en
  ja en
  ko en
  nl en
  no en
  pl en
  pt en
  ru en
  sl en
  sv en
  tr en
  zh en
)

run_verify() {
  local target_lang="$1"
  local home_lang="$2"
  local pair="${target_lang}-${home_lang}"
  local flags="review/flagged-${pair}.json"

  if [ -f "$flags" ]; then
    echo "[$pair] Already done, skipping"
    return
  fi

  # Check if pair exists in DB
  count=$(PYTHONPATH="$SCRIPT_DIR" python3 -c "
from morpheme_db import get_db, get_done_ids
conn = get_db()
ids = get_done_ids(conn, '$target_lang', '$home_lang')
conn.close()
print(len(ids))
" 2>/dev/null || echo "0")

  if [ "$count" = "0" ]; then
    echo "[$pair] No entries in DB — skipping"
    return
  fi

  echo "[$pair] Starting ($count entries)..."
  python "$SCRIPT_DIR/verify_dict.py" \
    --target-lang "$target_lang" \
    --home-lang "$home_lang" \
    --api-key "$MORPHEME_SORT_ANTHROPIC_API_KEY" \
    2>&1 | sed "s|^|[$pair] |"
  echo "[$pair] Done."
}

export -f run_verify
export MORPHEME_SORT_ANTHROPIC_API_KEY
export SCRIPT_DIR

# Build array of "target home" pairs for xargs
pair_args=()
for ((i=0; i<${#PAIRS[@]}; i+=2)); do
  pair_args+=("${PAIRS[$i]} ${PAIRS[$i+1]}")
done

printf '%s\n' "${pair_args[@]}" | xargs -P "$MAX_JOBS" -I {} bash -c 'run_verify $@' _ {}

echo ""
echo "All done! Flags are in the DB and review/flagged-*-en.json"
