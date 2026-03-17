#!/bin/bash
# Run verify_dict.py on all entries with to_verify=1, one pair at a time.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

PAIRS=$(PYTHONPATH="$SCRIPT_DIR" python3 -c "
import sqlite3
import os
conn = sqlite3.connect(os.path.join('$SCRIPT_DIR', '..', 'morpheme_dicts.db'))
rows = conn.execute('''
    SELECT DISTINCT target_lang, home_lang FROM entries
    WHERE to_verify=1 ORDER BY target_lang, home_lang
''').fetchall()
for r in rows:
    print(f'{r[0]} {r[1]}')
")

while IFS=' ' read -r target home; do
    echo ""
    echo "=== Verifying $target-$home ==="
    python3 "$SCRIPT_DIR/verify_dict.py" \
        --target-lang "$target" \
        --home-lang "$home" \
        --to-verify \
        --api-key "$MORPHEME_SORT_ANTHROPIC_API_KEY"
done <<< "$PAIRS"

echo ""
echo "All done."
