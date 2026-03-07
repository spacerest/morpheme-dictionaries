#!/bin/bash
# Run verify_dict.py on all entries with to_verify=1, one pair at a time.

set -e

PAIRS=$(python3 -c "
import sqlite3
conn = sqlite3.connect('morpheme_dicts.db')
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
    python3 verify_dict.py --target-lang "$target" --home-lang "$home" --to-verify
done <<< "$PAIRS"

echo ""
echo "All done."
