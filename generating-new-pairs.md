# Generating New Language Pairs

## Two Pipelines

### 1. Fresh pair (e.g. en-ga, ga-en)
Use when: the target language is new, or you want entries generated from scratch.

```bash
# On the server (Claude API):
python generate_claude.py --target ga --home en --word-list word-lists/ga.txt

# Then verify (also on server):
python verify_dict.py --target-lang ga --home-lang en --to-verify
```

**Before running:**
- Make sure a word list exists at `word-lists/ga.txt`
- Optionally create `prompts/ga-en/system.txt` for language-specific guidance (see ja-en for an example). If no pair-specific file exists, the base `prompts/system.txt` is used.
- Optionally create `prompts/ga-en/glossary.txt` for common morphemes. Without it, Sonnet invents labels from scratch — fine for a first pass, but expect inconsistency across entries. Create the glossary after the first run (see todos: ru-de post-verify cleanup as an example).

---

### 2. Cross-pair from existing pairs (e.g. ru-hi, de-hi)
Use when: both languages already have entries vs. English (e.g. ru-en and hi-en both exist). This re-glosses the existing Russian entries into Hindi rather than generating from scratch.

**Step 1 — Google Translate (run locally, needs internet):**
```bash
python generate_cross_pairs.py --langs ru hi
```
This fills `translation_short` and `example_translation` for the new pair.

**Step 2 — Claude regloss (run on server):**
```bash
python regloss_cross_pairs.py --target-lang ru --home-lang hi
```
This fills `homeLang` tile labels, `homeLangDetails`, and `translation_long`.

**Step 3 — Verify (run on server):**
```bash
python verify_dict.py --target-lang ru --home-lang hi --to-verify
```

---

## Do New Pairs Need the Verify Pass?

**Yes, always** — regardless of pipeline. The verify pass now does significant work beyond just catching errors:
- Fills `pos` and `register`
- Fills `morpheme_type` for all parts
- Generates missing `homeLangDetails`
- Fills missing article/gender (e.g. Russian noun gender)
- Fixes notation violations (slashes, parentheses on semantic content, etc.)
- Catches pre-solve mistakes (morpheme glossed with compound's meaning)

---

## Which Prompts Affect What

| Prompt | Used by | Updated with new guidelines? |
|--------|---------|------------------------------|
| `prompts/system.txt` | `generate_claude.py` (fresh pairs) | ✅ Yes |
| `prompts/XX-YY/system.txt` | `generate_claude.py` (pair-specific override) | ⚠️ See below |
| `prompts/verify.txt` | `verify_dict.py` (all pairs) | ✅ Yes |
| `prompts/fix.txt` | `fix_dict.py` (legacy, rarely needed now) | ✅ Yes |
| `prompts/regloss_cross.txt` | `regloss_cross_pairs.py` (cross-pairs only) | ⚠️ See below |

### Pair-specific system prompts are full overrides

`prompts/de-en/system.txt`, `prompts/ja-en/system.txt`, `prompts/hi-en/system.txt`, `prompts/sw-en/system.txt`, `prompts/eo-en/system.txt`, `prompts/en-hi/system.txt`, `prompts/en-sw/system.txt` — these **replace** the base `system.txt` entirely when that pair is generated. They do NOT inherit from the base.

All pair-specific prompts and `regloss_cross.txt` now include:
- Building-block principle ("don't pre-solve the puzzle")
- No slashes in homeLang values
- `homeLangDetails` style: "As in X..." not "From X...", no em-dashes

---

## Quick Reference: en-ga (new fresh pair)

```bash
# 1. Create word list
# word-lists/ga.txt — one word per line

# 2. Optionally create prompts/ga-en/system.txt for Gaelic-specific rules
#    (mutations, lenition, initial mutations, etc.)
#    If omitted, base system.txt is used.

# 3. Generate
python generate_claude.py --target ga --home en --word-list word-lists/ga.txt

# 4. Verify (fills pos, register, morpheme types, homeLangDetails, fixes notation)
python verify_dict.py --target-lang ga --home-lang en --to-verify

# 5. Export
python export_to_json.py --target-lang ga --home-lang en --app
```

## Quick Reference: ru-hi (cross-pair from existing)

```bash
# Locally (Google Translate):
python generate_cross_pairs.py --langs ru hi

# On server (Claude):
python regloss_cross_pairs.py --target-lang ru --home-lang hi
python verify_dict.py --target-lang ru --home-lang hi --to-verify

# Export
python export_to_json.py --target-lang ru --home-lang hi --app
```
