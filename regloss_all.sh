#!/usr/bin/env bash
# Re-gloss the English reference dict into all 16 home languages.
# Run from the project root: bash regloss_all.sh

set -e

declare -A LANGS=(
  [Arabic]=ar
  [Chinese]=zh
  [Danish]=da
  [Dutch]=nl
  [Finnish]=fi
  [French]=fr
  [German]=de
  [Italian]=it
  [Japanese]=ja
  [Korean]=ko
  [Norwegian]=no
  [Polish]=pl
  [Portuguese]=pt
  [Russian]=ru
  [Spanish]=es
  [Swedish]=sv
  [Turkish]=tr
)

for lang in "${!LANGS[@]}"; do
  code="${LANGS[$lang]}"
  echo "=== $lang (en-$code) ==="
  python regloss_dict.py \
    --input dicts/en-ref.json \
    --output "dicts/en-$code.json" \
    --source-home English \
    --home "$lang" \
    --api-key "$MORPHEME_SORT_ANTHROPIC_API_KEY" 
    
done

echo ""
echo "All done!"
