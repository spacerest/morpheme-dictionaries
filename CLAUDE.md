# Claude Code Instructions

## API Cost Tracking

**Every script that makes Anthropic API calls must use `CostTracker`.**

```python
from cost_tracker import CostTracker

tracker = CostTracker(script="script_name", pair="xx-yy", model=model)

# after each API call:
tracker.add(response.usage)

# at end of script (prints summary + appends row to api_costs.md):
tracker.finish()
```

- `pair` should be the language pair being processed (e.g. `"de-en"`) or `"mixed"` if the script handles multiple pairs in a single run
- Call `tracker.finish()` once per script run (or once per pair if iterating over multiple pairs)
- Do not skip this for "small" or "one-off" scripts — even single-call scripts should track

Costs are logged to `api_costs.md` in the project root (append-only markdown table).

## Models

- Generation: `claude-sonnet-4-6` (default in `scripts/generate_claude.py`)
- Verification / fix / regloss: `claude-haiku-4-5-20251001` (cheaper, fast)
- Glossary creation: `claude-sonnet-4-6`

## DB

- Single source of truth: `morpheme_dicts.db`
- Always use `morpheme_db.get_db()` — never open sqlite3 directly in new scripts
- `target_lang` = language being learned, `home_lang` = gloss language

## Environment

- `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY` are in `.env` (auto-loaded by most scripts)
- Google Translate scripts must run locally (server has no outbound internet)
