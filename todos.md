## ru-de post-verify cleanup
- [] create ru-de glossary after verify pass — check for inconsistent labels across entries (e.g. -ность glossed as -heit in some, -keit in others) and canonicalize

## Issues to look up and fix in db
- [] ru-en: audit for more verbs with incomplete splitting — prefix correctly split but infinitive ending not extracted as its own part (e.g. пере + водить instead of пере + вод + ить). Query: parts ending in -ть/-ти/-ить/-еть/-ать/-уть longer than ~4 chars that start with a known prefix.
- [] some parts have incorrect part_role — the backfill heuristic (parentheses → grammatical, '-' → linking) will have mis-tagged some; audit and correct manually via SQL
- [] one-part words ("intellektuell")
- [] erzaehlen has wrong equivalent morpheme in english -- says "er" is "-er"
- [] entries with phantom `0(-)` parts (target_lang_text='0', home_lang_text='-') — query all affected entries and delete those parts. Known cases: unternehmung, Geburtstagskuchen, Weihnachtsbaum.

## General fixes
- [] morpheme/parts (?) table needs a column for a flag to mark morphemes translations that conflict with the common morpheme translation (i.e. ein is usually "in" in german, but sometimes "one" as in "einseitigkeit")
- [] related to above, maybe we should have two "ein" in morpheme/parts (?) table. a primary, secondary one? or would we have "ein" (in) and "eins" (one)

## Sanity check improvements (done)
- [x] Added checks 8 and 9: translationShort in exampleTranslation and word_id in exampleSentence (fuzzy + substring)
- [x] Per-language fuzzy thresholds (agglutinative 0.55, Slavic 0.58–0.60, Germanic 0.65, Romance 0.68, en 0.72, zh 0.80)
- [x] Handles comma/semicolon/slash-separated alternatives (e.g. "freedom, liberty")
- [x] Strips parenthetical qualifiers (e.g. "to call (by phone)") before matching
- [x] Strips "to " infinitive prefix before matching English verbs
- [x] Checks 8–9 insert flags into verification_flags DB table with dedup

## Verification pass (to_verify=1 entries)

Run `bash verify_all_to_verify.sh` to verify all 2,291 entries currently marked `to_verify=1`
across 13 language pairs (da, de, eo, fi, fr, hi, it, nl, no, ru, sw, tr, zh — all vs en).

Why this matters: these entries were generated before the current prompt improvements (short
homeLang values, no slash synonyms, abbreviated grammatical labels). The verify pass uses a
second Claude call to flag likely errors — wrong morpheme splits, false cognates, bad article
assignments, unnatural example sentences, and context-specific morpheme mismatches (like
ein- = "in" vs "one"). Without this pass, bad entries can slip through into the app and
confuse learners. Flags go into the verification_flags table and review/flagged-*.json for
human review. After fixing, mark entries review_status='passed' and import=1 to include them
in exports.

## General todos
- [] before generating word lists for a new language, ask Claude to review and beef up the LANGUAGE_NOTES entry in generate_wordlists.py — add concrete good/bad examples, specific productive morphemes with meanings, and explicit AVOID instructions (see sl notes as a model)

- [] in export_to_json, for each part look up all other distinct home_lang_text values for the same target_lang_text across the pair (import=1 only, exclude `-` and parenthetical labels), and if any differ from the current gloss, append "Other equivalents of X: a, b." to homeLangDetails. Suppress if only one unique gloss exists. Clarify: dash restoration in label, placement when homeLangDetails already has content, whether to scope to import=1.

- [] add long translation to esperanto glossary
- [] add swahili glossary
- [] add possible age rating to words
- [] add possible "learning level" rating to words
- [] make 100-word, "ready" versions of all dicts to ship with the app
- [] take out arabic as a targetLang for now 
- [] add hindi glossary
- [] have a words list that keeps track of what languages we have that word available in
- [] inconsistency in parts table. i.e. look up "ship" in home_lang_text of parts table -- multiple similar entries for the same room in target_lang_text
- [] finish de-ru dict (ended around 60/116) and ru-de (ended around 160/541)
- [] generate hi and zh cross pairs with all other non-English languages (hi-en and zh-en already in DB with 100 and 233 entries; just run generate_cross_pairs.py locally — hi/zh are auto-included)
- [] figure out why export_to_json says no hi-en or en-hi or en-sw etc in db
- [x] decide on cross-pair generation strategy: two-step pipeline — Google Translate for translation_short (word_id) and example_translation; Claude (regloss_cross_pairs.py) for homeLang tile values and translation_long and home_lang_details
- [x] undersplit detection -- ran find_undersplit.py and fix_undersplit.py on all pairs. 140 entries auto-fixed (de-en: 98, ar-en: 24, ru-en: 12, fi-en: 2, others: 1 each). en-XX pairs were already clean.
- [] de-en: 3 Fugen-suffix undersplit cases need manual review -- geburtstagskuchen (geburtstags→geburtstag+s), sicherheitshalber (sicherheits→sicherheit+s), teilnehmerinn (→teilnehmerin+n). Fix by expanding the baked-in suffix into a separate linking part.
- [x] update generate_cross_pairs.py to only do Google Translate for translation_short + example_translation (done)
- [x] make prompt for anthropic calls related to generate_cross_pairs.py (done: prompts/regloss_cross.txt)
- [] zu and other short grammatical morphemes need shorter equivs (maybe move this to morpheme-dictionary repo)
- []  Relationship between Verkehr (traffic) and verkehrt (upside down)? (maybe move this to morpheme-dictionary)
- [x] German nouns need to be capitalized -- fixed in DB: 1939 renamed, 2 lowercase duplicates (beschreibung, entscheidung) deleted
- [] "disappointed" in hindi has first morpheme meaning "fully", which doens't make sense ("fully + hope" doesn't make disappointed). Google translate says first morpheme ni means prohibit
- [x] russian translations still blank? thought we filled those in like multiple times
- [] en-ru short_translations seem wordy. and they don't have gender indicated
- [] en-hi has "the" as article for all nouns (should be indicating gender?)
- [] more verbs in english word lists
- [] double check that all fi-en have more than one part
- [x] add dictionary for irish gaelic (ga-en, 99 entries, glossary created)
- [] add dictionary for welsh
- [] make a script that goes through home_lang looking for commas and slashes and chooses one and moves the other to an alternates column
- [] en-zh has some english in home_lang_text
- [] en-zh has some home_lang_text that says noun, adj in chinese but is marked semantic not grammatical

## Cross-pair pipeline gaps (de-ru, ru-de and future xx-yy pairs)

Three fields are currently missing from cross-pair entries and need to be fixed:

1. **translation_short + example_translation** (empty for de-ru/ru-de): Were accidentally
   cleared in a cleanup run. Need a `--fill-missing` flag in `generate_cross_pairs.py`
   that UPDATEs entries with empty values (instead of INSERT OR IGNORE skipping them).
   Run locally since it requires Google Translate API access.
   ```
   python generate_cross_pairs.py --langs de ru --fill-missing
   ```

2. **translation_long** (never generated): `regloss_cross_pairs.py` only fills
   `parts[].home_lang_text`. Update the script and `prompts/regloss_cross.txt` to also
   generate a `translationLong` per word (a fuller translation, like `translation_short`
   but with alternates/context, in the home language).

3. **home_lang_details on parts** (always NULL for cross pairs): The rich per-morpheme
   explanations (with examples of the morpheme in other words) are only generated for
   xx-en pairs by the main generation prompt. Update `prompts/regloss_cross.txt` and
   `regloss_cross_pairs.py` to optionally generate these too — or accept they stay NULL
   for cross pairs to save API cost.

**Root cause of wrong-language translations**: `translate()` had `source="en"` hardcoded.
For cross-pairs the source must be `target_lang` (the language the word/sentence is in).
Fixed in generate_cross_pairs.py — translate() now takes a `source_lang` parameter.

Fix order:
1. Clear bad translation_short and example_translation for de-ru and ru-de (they may have
   wrong-language values from the buggy run):
   ```python
   UPDATE entries SET translation_short='', example_translation='' WHERE target_lang='de' AND home_lang='ru';
   UPDATE entries SET translation_short='', example_translation='' WHERE target_lang='ru' AND home_lang='de';
   ```
2. Locally (Google Translate, now with correct source lang):
   `python generate_cross_pairs.py --langs de ru --fill-missing`
3. Server (Claude): `python regloss_cross_pairs.py --target-lang de --home-lang ru`
4. Server (Claude): `python regloss_cross_pairs.py --target-lang ru --home-lang de`

## Getting 100-word dicts ready to ship

For each language pair, in priority order:

1. **Check counts**: how many entries exist, how many have `import=1`
   ```sql
   SELECT target_lang, home_lang, COUNT(*) total,
          SUM(import) importable
   FROM entries GROUP BY target_lang, home_lang ORDER BY importable DESC;
   ```

2. **Spot-check quality**: read 10–15 random entries, look for bad morpheme splits,
   wrong homeLang values, or broken example sentences
   ```
   python spot_check.py --target-lang de --home-lang en --n 15
   ```

3. **Run verify**: LLM verification pass flags suspicious entries
   ```
   python verify_dict.py --target-lang de --home-lang en
   ```

4. **Fix flagged issues**: review open flags, fix or dismiss each one
   ```
   python fix_dict.py --target-lang de --home-lang en
   ```

5. **Review morpheme consistency**: look for the same morpheme glossed differently
   across entries (e.g. "-ship" as "state" vs "quality" vs "state/quality").
   Do NOT bulk-normalize — some variation is correct (e.g. ein- = "in" vs "one").
   Fix obvious formatting drift manually; let verify catch context-specific cases.

6. **Curate to 100**: mark exactly 100 entries `import=1` — prefer words with
   2–4 clear parts, good morpheme variety, natural example sentences.
   Set the rest to `import=0`.

7. **Export and test in app**:
   ```
   python export_to_json.py --target-lang de --home-lang en --app
   ```

8. **Mark pair as shipped** in `lang_pair_meta`:
   ```python
   set_pair_meta(conn, 'de', 'en', status='active', notes='v1 shipped, 100 words')
   ```

