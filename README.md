# Morpheme Dictionaries

JSON dictionaries for a language-learning word puzzle game. Each entry splits a target-language word into its morphemes, with glosses, a translation, and an example sentence.

---

## Using a dictionary in the game

The game is [Water Sorting Word Roots](../water_sorting_word_roots). Each dictionary is a JSON file covering one language pair, e.g. German words with English glosses (`de-en.json`).

### Getting a dictionary

Pre-made dictionaries are in the `dicts/` folder. Available pairs include `de-en`, `ru-en`, `ja-en`, `zh-en`, `fi-en`, `tr-en`, `eo-en`, `ga-en`, `sl-en`, and cross-pairs like `de-zh`, `ja-sl`, `ru-de`.

If you want a custom dictionary (different word list, different language pair, or different gloss language), see the **Step-by-step guide** below — or ask someone with API access to generate one for you.

### Loading into the game

1. Copy or download the `.json` file you want to your device.
2. Open the game and tap the **menu button** to go to Settings.
3. Tap **Load Custom Dictionary** and select the JSON file.
4. The words are added to the existing dictionary. You can load multiple files.

To use a dictionary as the default (replacing the built-in words), place it at `app/src/main/assets/` and rebuild the app.

---

## For developers

All dictionary data lives in a single SQLite database (`morpheme_dicts.db`). JSON files in `dicts/` are exported from it — never edit them directly.

## Quick start

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Generate entries for a new word list (writes to DB):
python scripts/generate_claude.py \
  --input word-lists/de-en-words.txt \
  --home English --target German

# Export to JSON for the app:
python scripts/export_to_json.py --all
```

---

## Database

### Browsing the DB

Install [DB Browser for SQLite](https://sqlitebrowser.org/):

```bash
sudo apt-get install sqlitebrowser   # Ubuntu/Debian
sqlitebrowser morpheme_dicts.db
```

Useful queries:

```sql
-- Per-pair dashboard: status, goal, current count, reviewed count
SELECT m.target_lang || '-' || m.home_lang AS pair,
       m.status, m.priority, m.target_count,
       COUNT(e.word_id) AS current,
       SUM(e.review_status = 'passed') AS passed
FROM lang_pair_meta m
LEFT JOIN entries e USING (target_lang, home_lang)
GROUP BY m.target_lang, m.home_lang
ORDER BY m.priority NULLS LAST, pair;

-- Show all German-English entries with their parts (entry_overview view)
SELECT * FROM entry_overview WHERE pair = 'de-en';

-- Entries still needing review for a pair
SELECT word_id, translation_short FROM entries
WHERE target_lang='de' AND home_lang='en' AND review_status IS NULL
ORDER BY rowid;

-- Find open verification flags
SELECT word_id, category, issue FROM verification_flags
WHERE target_lang='en' AND home_lang='tr' AND status='open';
```

### DB tables

| Table | Description |
|---|---|
| `entries` | One row per word. Primary key: `(target_lang, home_lang, word_id)` |
| `parts` | Morpheme breakdown. FK → entries, ordered by `part_index` |
| `lang_pair_meta` | Per-pair goals, priority, status, and notes |
| `verification_flags` | Flags from `verify_dict.py` (open/fixed/dismissed) |
| `known_discrepancies` | Confirmed errors fed back into generation prompts |
| `morphemes` | Glossary entries from `prompts/*/glossary.txt` |
| `canonical_labels` | Canonical grammatical label forms per home language (drives prompt injection) |
| `wordlist_words` | Word list tracking (pending/done/skipped) |

#### entries columns of note

| Column | Description |
|---|---|
| `review_status` | `NULL` = unreviewed, `'passed'` = approved, `'needs_work'` = flagged |
| `import` | `1` = include in exports, `0` = exclude |
| `export_dict_name` | Override the output filename for this entry's pair |

#### lang_pair_meta

Tracks per-language-pair goals and status. Auto-populated from `entries` on first open; edit directly in DB Browser or via `set_pair_meta()`.

| Column | Description |
|---|---|
| `status` | `active`, `parked`, or `shipped` |
| `priority` | Integer rank (1 = highest). NULL = unprioritised |
| `target_count` | Goal number of words to ship for this pair |
| `notes` | Free-text notes |

Dashboard query:

```sql
SELECT m.target_lang || '-' || m.home_lang AS pair,
       m.status, m.priority, m.target_count,
       COUNT(e.word_id) AS current,
       SUM(e.review_status = 'passed') AS passed,
       m.notes
FROM lang_pair_meta m
LEFT JOIN entries e USING (target_lang, home_lang)
GROUP BY m.target_lang, m.home_lang
ORDER BY m.priority NULLS LAST, pair;
```

Or from Python:

```python
from morpheme_db import get_db, set_pair_meta
conn = get_db()
set_pair_meta(conn, 'de', 'en', priority=1, target_count=100, notes='needs curation')
set_pair_meta(conn, 'ar', 'en', status='parked')
```

### Import / export

```bash
# Import all existing JSON + review files into DB (safe to re-run)
python scripts/import_to_db.py

# Export DB → JSON (default: dicts/)
python scripts/export_to_json.py --all
python scripts/export_to_json.py --target-lang tr --home-lang en

# Export to app assets
python scripts/export_to_json.py --all --app
```

---

## Step-by-step guide

### 1. Prerequisites

```bash
pip install anthropic
```

Get an Anthropic API key from [console.anthropic.com](https://console.anthropic.com) and set it:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# or use the project-specific alias:
export MORPHEME_SORT_ANTHROPIC_API_KEY=sk-ant-...
```

### 1b. Seed canonical labels (once per home language)

Canonical grammatical labels (e.g. `(verb)` not `(inf)`, `(past participle)` not `(pp)`) are stored in the DB and injected into generation and verification prompts automatically. English is included. For a new home language, seed it before generating:

```bash
python scripts/seed_canonical_labels.py              # English (already done)
python scripts/seed_canonical_labels.py --home-lang de

# List current labels for a language:
python scripts/seed_canonical_labels.py --home-lang en --list
```

For a home language not yet defined in `seed_canonical_labels.py`, ask claude to add a `LABELS["xx"]` entry (translate from the English list), then run the script.

### 2. Generate a word list

```bash
python scripts/generate_wordlists.py --lang de   # generates word-lists/de-en-words.txt
```

Or write one manually — one word per line. Use `filter_words.py` to clean a raw corpus:

```bash
python scripts/filter_words.py \
  --input word-lists/raw.txt \
  --output word-lists/filtered.txt \
  --min-len 6 --max-len 25
```

### 3. Generate the dictionary

```bash
python scripts/generate_claude.py \
  --input word-lists/de-en-words.txt \
  --home English --target German
```

Entries are written to `morpheme_dicts.db` after every batch. If interrupted, re-run the same command — words already in the DB are skipped automatically.

**Cost:** roughly €4–6 per 1000 words using the default Sonnet model. Use `--model claude-haiku-4-5-20251001` to cut costs by ~4x at some quality trade-off.

#### generate_claude.py options

| Flag | Default | Description |
|---|---|---|
| `--input` | (required) | Word list file |
| `--home` | (required) | Home language, e.g. `English` |
| `--target` | (required) | Target language, e.g. `German` |
| `--api-key` | env var | Anthropic API key |
| `--batch-size` | `15` | Words per API call |
| `--model` | `claude-sonnet-4-6` | Claude model |
| `--db` | `morpheme_dicts.db` | DB file path |

### 3b. Re-gloss for additional home languages

Generate once, translate cheaply for each additional home language using Haiku:

```bash
# Generate the reference dict (English words, English glosses)
python scripts/generate_claude.py \
  --input word-lists/en-words.txt \
  --target English --home English

# Re-gloss for each additional home language (reads/writes DB)
python scripts/regloss_dict.py \
  --source-pair en-en --target-pair en-de \
  --source-home English --home German

python scripts/regloss_dict.py \
  --source-pair en-en --target-pair en-fr \
  --source-home English --home French
```

Re-glossing translates `homeLang`, `homeLangDetails`, `translationShort`, `translationLong`, and `exampleTranslation`, leaving all target-language content unchanged.

#### regloss_dict.py options (DB mode)

| Flag | Default | Description |
|---|---|---|
| `--source-pair` | (required) | Source lang pair in DB, e.g. `en-en` |
| `--target-pair` | (required) | Output lang pair, e.g. `en-de` |
| `--source-home` | (required) | Home language name of the source |
| `--home` | (required) | Target home language name |
| `--api-key` | env var | Anthropic API key |
| `--batch-size` | `10` | Entries per API call |
| `--model` | `claude-haiku-4-5-20251001` | Claude model |

### 4. Sanity check

Catch structural problems before spending API calls on LLM verification:

```bash
python scripts/sanity_check.py                                   # all pairs in DB
python scripts/sanity_check.py --quiet                           # only show pairs with issues
python scripts/sanity_check.py --target-lang de --home-lang en   # single pair
python scripts/sanity_check.py --all                             # include import=0 entries
```

Checks performed:
1. `targetLang` parts concatenate back to the word ID
2. Missing `homeLang` on non-trivial parts
3. Circular `homeLang` (morpheme used as its own gloss)
4. Empty required fields (`translationShort`, `exampleSentence`)
5. Suspiciously few or many parts (0 or >5)
6. Duplicate word IDs
7. Parts with empty `targetLang`
8. `translationShort` not found in `exampleTranslation` (fuzzy + substring, handles comma-separated alternatives and parenthetical qualifiers)
9. `word_id` not found in `exampleSentence` (fuzzy + substring)

Issues from checks 8–9 are also inserted into the `verification_flags` DB table for review.

### 5. Verify the output

Run a second, cheaper pass to catch errors like false cognates, frozen compounds, and wrong morpheme boundaries:

```bash
python scripts/verify_dict.py --target-lang de --home-lang en
```

Flags are inserted into the `verification_flags` DB table and also written to `review/flagged-de-en.json` for human review.

### 5b. Resolve slash/comma glosses

After generation (and again after verification), some `homeLang` values may contain slash- or comma-separated alternatives (e.g. `"away/off"`, `"make, do"`). Run this to pick the best single gloss using Haiku and store the discarded options in `homeLangAlternates`:

```bash
python scripts/fix_slash_glosses.py --target-lang de --home-lang en

# Preview without making changes:
python scripts/fix_slash_glosses.py --target-lang de --home-lang en --dry-run

# Limit to a specific word set:
python scripts/fix_slash_glosses.py --target-lang de --home-lang en --word-set first_release_dictionary
```

Only parts with `part_role='semantic'` are processed — grammatical labels like `"(noun suffix)"` are skipped automatically.

### 6. Log confirmed issues

For each flag you agree with, it gets stored in `known_discrepancies`. Future generation runs will automatically avoid those errors. You can also add them manually:

```bash
# Insert directly into DB (or edit review/discrepancies.json + re-import)
sqlite3 morpheme_dicts.db "
INSERT INTO known_discrepancies (word_id, category, field, issue, correction, status)
VALUES ('beispiel', 'frozen_compound', 'parts[1].homeLang',
        'spiel here is archaic spel (narrative), not spielen (to play)',
        'note in homeLangDetails that this is a frozen compound', 'confirmed');
"
```

### 7. Fix flagged entries

```bash
python scripts/fix_dict.py --target-lang de --home-lang en
```

Fixes are written back to the DB and flags are marked resolved.

### 8. Interactive cleanup (optional)

For fine-grained review of individual entries, paste `prompts/cleanup-prompt.md` into a Claude chat session along with the entries. It walks through them one by one.

---

## Full pipeline (XX-en dicts)

```bash
bash scripts/pipeline_xxen.sh
```

Or step by step:

```bash
bash scripts/generate_all_xxen.sh           # generate all XX-en dicts
python scripts/sanity_check.py              # structural checks
bash scripts/verify_all_xxen.sh             # LLM verification (parallel, 3 jobs)
# fix step runs automatically in pipeline_xxen.sh
python scripts/export_to_json.py --all      # export to dicts/ when ready
```

---

## Dictionary format

```json
{
  "words": [
    {
      "id": "geburtstag",
      "article": "der",
      "parts": [
        {"targetLang": "geburt", "homeLang": "birth"},
        {"targetLang": "s", "homeLang": "-", "homeLangDetails": "A Fugenlaut connecting element..."},
        {"targetLang": "tag", "homeLang": "day"}
      ],
      "translationShort": "birthday",
      "translationLong": "",
      "exampleSentence": "Heute ist mein Geburtstag!",
      "exampleTranslation": "Today is my birthday!"
    }
  ]
}
```

**Fields:**

| Field | Description |
|---|---|
| `id` | The target-language word |
| `article` | Definite article if applicable (`der`, `die`, `das`, `le`, etc.), or `""` |
| `displayPrefix` | Optional particle shown before the word in UI but not a game block (e.g. `sich` for reflexive verbs) |
| `parts` | Array of morphemes: `targetLang`, `homeLang`, optional `homeLangDetails` |
| `translationShort` | Natural 1–4 word translation |
| `translationLong` | Longer or alternate meanings, or `""` |
| `exampleSentence` | A sentence in the target language |
| `exampleTranslation` | Translation of the example |
| `flag` | Present only on entries with unusual morpheme counts |

---

## Prompts

Prompts are organized by language pair. The script looks for a pair-specific file first (e.g. `prompts/de-en/system.txt`), then falls back to the top-level shared file.

| File | Description |
|---|---|
| `prompts/system.txt` | Generic system prompt (fallback for new language pairs) |
| `prompts/user.txt` | User message template sent with each batch |
| `prompts/verify.txt` | Verification prompt for the second-pass checker |
| `prompts/{pair}/system.txt` | Language-pair-specific system prompt with tuned examples |
| `prompts/{pair}/glossary.txt` | Morpheme glosses; auto-fills `homeLangDetails` during generation |
| `prompts/regloss.txt` | Prompt for re-glossing into a different home language |
| `prompts/cleanup-prompt.md` | Interactive one-by-one review prompt |

Language pair directories use ISO 639-1 codes: `de-en` (German → English), `en-fr` (English → French), etc.

**Supported pairs:** `de-en`, `fr-en`, `es-en`, `it-en`, `pt-en`, `ru-en`, `zh-en`, `ja-en`, `ko-en`, `ar-en`, `nl-en`, `pl-en`, `sv-en`, `da-en`, `no-en`, `fi-en`, `tr-en`, and `en-*` versions of each.

### Adding a new language pair

```bash
# Populate the glossary template for the new pair
nano prompts/fr-en/glossary.txt

# Optionally copy and adapt the system prompt
cp prompts/de-en/system.txt prompts/fr-en/system.txt
# edit to replace German examples with French ones
```

If no `system.txt` exists in the pair directory, the script falls back to `prompts/system.txt`.

---

## Files

```
morpheme_dicts.db           Single source of truth (SQLite)

scripts/
  morpheme_db.py            DB helper module (schema + CRUD)
  generate_claude.py        Claude-based dictionary generator (→ DB)
  regloss_dict.py           Re-gloss a dict for a different home language (→ DB)
  verify_dict.py            Second-pass verification using Claude (flags → DB)
  fix_dict.py               Fix flagged entries using Claude (→ DB)
  fix_slash_glosses.py      Resolve slash/comma homeLang values using Haiku (→ DB)
  sanity_check.py           Fast structural checks (reads DB)
  import_to_db.py           One-time import from JSON files into DB
  export_to_json.py         Export DB → JSON files (for app deployment)
  generate_wordlists.py     Generate word lists via Claude
  filter_words.py           Filter and deduplicate a raw word list
  generate_all_xxen.sh      Generate all XX-en dicts
  verify_all_xxen.sh        Verify all XX-en dicts (parallel)
  pipeline_xxen.sh          Full pipeline: generate → check → verify → fix

dicts/
  de-en.json                German-English (exported from DB)
  en-tr.json                English-Turkish
  ...                       (all other language pairs)

word-lists/
  de-en-words.txt           German word list for generation
  ...

review/
  discrepancies.json        Confirmed errors (feeds back into generation)
  flagged-*.json            Output from verify_dict.py, for human review

prompts/
  system.txt                Generic system prompt (fallback)
  user.txt                  Shared batch message template
  verify.txt                Verification prompt
  de-en/                    German → English (complete)
    system.txt
    glossary.txt
  en-de/                    English → German (glossary needs translation)
    glossary.txt
  fr-en/ es-en/ ru-en/ ...  Other xx-en pairs
  en-fr/ en-es/ en-ru/ ...  Other en-xx pairs
```
