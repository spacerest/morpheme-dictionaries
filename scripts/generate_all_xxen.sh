#!/usr/bin/env bash
# Generate XX-en morpheme dicts for all languages that have a word list.
# Run from project root: bash scripts/generate_all_xxen.sh --api-key "$MORPHEME_SORT_ANTHROPIC_API_KEY"
#
# Results are written to the SQLite DB (morpheme_dicts.db).
# generate_claude.py resumes mid-dict automatically — words already in DB are skipped.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
set -e

declare -A LANG_NAMES
LANG_NAMES[ar]="Arabic"
LANG_NAMES[da]="Danish"
LANG_NAMES[de]="German"
LANG_NAMES[es]="Spanish"
LANG_NAMES[fi]="Finnish"
LANG_NAMES[fr]="French"
LANG_NAMES[it]="Italian"
LANG_NAMES[ja]="Japanese"
LANG_NAMES[ko]="Korean"
LANG_NAMES[nl]="Dutch"
LANG_NAMES[no]="Norwegian"
LANG_NAMES[pl]="Polish"
LANG_NAMES[pt]="Portuguese"
LANG_NAMES[ru]="Russian"
LANG_NAMES[sl]="Slovenian"
LANG_NAMES[sv]="Swedish"
LANG_NAMES[tr]="Turkish"
LANG_NAMES[zh]="Mandarin Chinese"

API_KEY_ARG=""
for arg in "$@"; do
  if [[ "$prev" == "--api-key" ]]; then
    API_KEY_ARG="--api-key $arg"
  fi
  prev="$arg"
done

for code in "${!LANG_NAMES[@]}"; do
  lang="${LANG_NAMES[$code]}"
  wordlist="word-lists/${code}-en-words.txt"

  if [ ! -f "$wordlist" ]; then
    echo "=== $lang ($code): skipping (no word list at $wordlist) ==="
    continue
  fi

  echo "=== $lang ($code) ==="
  python "$SCRIPT_DIR/generate_claude.py" \
    --input "$wordlist" \
    --target "$lang" \
    --home English \
    $API_KEY_ARG

done

echo ""
echo "All done!"
