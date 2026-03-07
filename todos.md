## Issues to look up and fix in db
- [] one-part words ("intellektuell")
- [] erzaehlen has wrong equivalent morpheme in english -- says "er" is "-er"

## General fixes
- [] morpheme/parts (?) table needs a column for a flag to mark morphemes translations that conflict with the common morpheme translation (i.e. ein is usually "in" in german, but sometimes "one" as in "einseitigkeit")
- [] related to above, maybe we should have two "ein" in morpheme/parts (?) table. a primary, secondary one? or would we have "ein" (in) and "eins" (one)

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
- [] figure out why export_to_json says no hi-en or en-hi or en-sw etc in db
- [] decide on cross-pair generation strategy (de-ru, fr-es, etc.): Google Translate is fast and cheap but likely too lossy for morpheme glosses (short context-dependent labels like "opposite" or "(cause)" don't translate cleanly). Claude regloss is higher quality but costs more API calls. Consider: use Google Translate only for translation_short/long/exampleTranslation, and use Claude regloss (or manual glossaries) for the homeLang tile values which matter most for gameplay.
- [x] undersplit detection -- ran find_undersplit.py and fix_undersplit.py on all pairs. 140 entries auto-fixed (de-en: 98, ar-en: 24, ru-en: 12, fi-en: 2, others: 1 each). en-XX pairs were already clean.
- [] de-en: 3 Fugen-suffix undersplit cases need manual review -- geburtstagskuchen (geburtstags→geburtstag+s), sicherheitshalber (sicherheits→sicherheit+s), teilnehmerinn (→teilnehmerin+n). Fix by expanding the baked-in suffix into a separate linking part.

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

