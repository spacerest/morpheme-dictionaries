#!/usr/bin/env bash
# Full pipeline for XX-en dicts: wordlists → generate → sanity check → verify → fix
# Run from project root: bash scripts/pipeline_xxen.sh
#
# Each step skips work that's already done, so it's safe to re-run after interruption.
# All data is stored in morpheme_dicts.db.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
set -e

MAX_FIX_JOBS=3

echo "============================================"
echo "Step 1: Generate XX-en dicts"
echo "============================================"
bash "$SCRIPT_DIR/generate_all_xxen.sh"
echo ""

echo "============================================"
echo "Step 2: Sanity check"
echo "============================================"
python "$SCRIPT_DIR/sanity_check.py" || true
echo ""

echo "============================================"
echo "Step 3: Verify (LLM quality check)"
echo "============================================"
bash "$SCRIPT_DIR/verify_all_xxen.sh"
echo ""

echo "============================================"
echo "Step 4: Fix flagged entries"
echo "============================================"
fix_count=0
pids=()

# Get all XX-en pairs that have open flags in the DB
pairs=$(PYTHONPATH="$SCRIPT_DIR" python3 -c "
from morpheme_db import get_db
conn = get_db()
rows = conn.execute(
    \"SELECT DISTINCT target_lang, home_lang FROM verification_flags WHERE status='open' AND home_lang='en' ORDER BY target_lang\"
).fetchall()
conn.close()
for row in rows:
    print(row['target_lang'], row['home_lang'])
" 2>/dev/null || true)

if [ -z "$pairs" ]; then
  echo "No open flags found — nothing to fix."
else
  while IFS=' ' read -r target_lang home_lang; do
    [ -z "$target_lang" ] && continue
    echo "[$target_lang-$home_lang] Fixing..."
    python "$SCRIPT_DIR/fix_dict.py" --target-lang "$target_lang" --home-lang "$home_lang" &
    pids+=($!)
    fix_count=$((fix_count + 1))

    # Throttle parallel jobs
    while [ "$(jobs -r | wc -l)" -ge "$MAX_FIX_JOBS" ]; do
      sleep 2
    done
  done <<< "$pairs"

  # Wait for all fix jobs to finish
  for pid in "${pids[@]}"; do
    wait "$pid"
  done

  echo "Fixed $fix_count dicts."
fi

echo ""
echo "============================================"
echo "Pipeline complete!"
echo "Tip: re-run 'python scripts/sanity_check.py' to confirm."
echo "Export to JSON: python scripts/export_to_json.py --all"
echo "============================================"
