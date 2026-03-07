#!/usr/bin/env python3
"""
Generate word lists for each target language for XX-en dictionaries.

Produces word-lists/{lang}-en-words.txt for each language, ready to feed
into generate_claude.py with --target [Language] --home English.

Also inserts the new words into the wordlist_words DB table as 'pending'.

Usage:
    python generate_wordlists.py --api-key "$MORPHEME_SORT_ANTHROPIC_API_KEY"
    python generate_wordlists.py --lang de fr es  # specific languages only
"""

import argparse
import os
import sys
import time
from pathlib import Path

import anthropic

WORD_LISTS_DIR = Path(__file__).parent / "word-lists"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_COUNT = 100

LANGUAGES = {
    "ar": "Arabic",
    "da": "Danish",
    "de": "German",
    "eo": "Esperanto",
    "es": "Spanish",
    "fi": "Finnish",
    "fr": "French",
    "hi": "Hindi",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ru": "Russian",
    "sl": "Slovenian",
    "sv": "Swedish",
    "sw": "Swahili",
    "tr": "Turkish",
    "zh": "Mandarin Chinese",
}



LANGUAGE_NOTES = {
    "ar": """\
  - Include words showing common root patterns (the trilateral root system is the key feature)
  - For example: roots like ك-ت-ب (k-t-b) appear in كَتَبَ (write), كاتِب (writer), مَكتَبة (library)
  - Each word needs to use at least 3 roots that appear in
  - Write in Arabic script""",

    "da": """\
  - Include compound words that break apart cleanly (Danish compounding is very productive)
  - Include words with common prefixes: for-, be-, ud-, af-, gen-, sam-
  - Include words with common suffixes: -hed, -lig, -else, -ning, -er, -isk""",

    "de": """\
  - Include compound nouns that decompose clearly (e.g. Handschuh = Hand+Schuh, Zeitgeist = Zeit+Geist)
  - Include verbs with separable prefixes (auf-, ab-, an-, durch-, über-, vor-, nach-, zu-)
  - Include words with productive suffixes (-heit, -keit, -ung, -schaft, -lich, -los, -bar)
  - Include words with inseparable prefixes (be-, er-, ge-, ver-, zer-, ent-, emp-)""",

    "es": """\
  - Include words with Latin-derived prefixes/suffixes that English speakers will recognize
  - Include words with productive suffixes: -ción/-sión, -dad/-tad, -mente, -oso, -ible/-able, -ismo, -ista
  - Include words with common prefixes: des-, in-/im-, re-, sub-, pre-, sobre-, entre-""",

    "fi": """\
  - Include words that clearly show agglutinative morphology (Finnish stacks suffixes)
  - Include words with productive derivational suffixes: -nen, -us/-ys, -uus/-yys, -minen, -ja/-jä
  - Include compound words where both parts are recognizable
  - Include words with common prefixes and case-related patterns""",

    "fr": """\
  - Include words with Latin-derived prefixes/suffixes recognizable to English speakers
  - Include words with productive suffixes: -tion/-sion, -ment, -ité, -eux/-euse, -able/-ible, -isme, -iste
  - Include words with common prefixes: dé-/des-, re-/ré-, in-/im-, sur-, sous-, pré-, anti-""",

    "it": """\
  - Include words with Latin-derived prefixes/suffixes recognizable to English speakers
  - Include words with productive suffixes: -zione/-sione, -mento, -ità, -oso, -abile/-ibile, -ismo, -ista
  - Include words with common prefixes: dis-, in-/im-, ri-, sub-, pre-, sopra-, inter-""",

    "ja": """\
  - Include compound words where both kanji contribute clear meaning (e.g. 電話 = 電+話, 図書館 = 図書+館)
  - Include words with productive kanji morphemes: 学, 化, 性, 者, 所, 機, 車, 力, 的, 無, 不, 再
  - Write in Japanese script (kanji with hiragana as natural)
  - Mix Sino-Japanese (音読み) compounds and native Japanese (訓読み) words
  - Include some 3-kanji compounds that decompose clearly (e.g. 図書館, 新幹線, 自動車)""",

    "ko": """\
  - Include compound words where both elements contribute clear meaning
  - Include words with productive Sino-Korean morphemes: 학(學), 화(化), 성(性), 자(者), 소(所), 기(機)
  - Include words with native Korean prefixes/suffixes: 새-, 헛-, 맏-, -이, -음, -기, -스럽다
  - Write in Hangul script
  - Mix native Korean and Sino-Korean vocabulary""",

    "nl": """\
  - Include compound words that break apart cleanly (Dutch compounding is very productive)
  - Include verbs with separable prefixes (aan-, af-, in-, op-, uit-, over-, door-, mee-)
  - Include words with productive suffixes: -heid, -lijk, -ing, -baar, -achtig, -schap
  - Include words with inseparable prefixes: be-, ge-, ver-, ont-, her-""",

    "no": """\
  - Include compound words (Norwegian compounding is very productive)
  - Include words with common prefixes: for-, be-, ut-, av-, gen-, sam-, over-, under-
  - Include words with productive suffixes: -het, -lig, -else, -ning, -er, -isk, -bar""",

    "pl": """\
  - Include words with productive verbal prefixes that change meaning clearly:
    przed- (before), po- (after/completive), przy- (arrival/addition), za- (begin/behind),
    nad- (over/above), pod- (under), prze- (through/re-), roz- (apart/spread), wy- (out)
  - Include words with common derivational suffixes: -ość, -anie/-enie, -owy/-ny, -nik, -ka
  - Write with Polish diacritics (ą, ć, ę, ł, ń, ó, ś, ź, ż)""",

    "pt": """\
  - Include words with Latin-derived prefixes/suffixes recognizable to English speakers
  - Include words with productive suffixes: -ção/-são, -dade/-tade, -mente, -oso, -ível/-ável, -ismo, -ista
  - Include words with common prefixes: des-, in-/im-, re-, sub-, pré-, sobre-, entre-""",

    "ru": """\
  - Include words with productive verbal prefixes that clearly change meaning:
    без- (without), вы- (out), за- (begin/behind), на- (onto), над- (above),
    от- (away), пере- (re-/over), по- (after/a bit), под- (under),
    при- (arrival), про- (through/past), раз- (apart), с- (together/from)
  - Include words with common derivational suffixes: -ость/-есть, -ание/-ение, -ный/-ский, -тель, -ник
  - Write in Cyrillic script""",

    "sl": """\
  - Include words with productive prefixes and suffixes
  - Include compound words where both parts are recognizable
  - Include words with common Slavic prefixes: pred-, po-, pri-, za-, nad-, pod-, pre-, raz-
  - Include words with common suffixes: -ost, -anje/-enje, -ni, -ec/-ka
  - Write with Slovenian diacritics (č, š, ž)""",

    "sv": """\
  - Include compound words that break apart clearly (Swedish compounding is very productive)
  - Include verbs with separable prefixes (an-, av-, be-, för-, in-, om-, upp-, ut-, över-)
  - Include words with productive suffixes: -het, -lig, -ning, -bar, -skap, -are, -else
  - Include words with inseparable prefixes: be-, för-, ge-, miss-""",

    "tr": """\
  - Include words that clearly show agglutinative morphology (Turkish stacks suffixes)
  - Include words with productive noun suffixes: -lık/-lik/-luk/-lük, -cı/-ci/-cu/-cü, -sız/-siz/-suz/-süz, -lı/-li/-lu/-lü
  - Include words with verb derivational suffixes: -mak/-mek stems, -ci (agent), -lik (state)
  - Include compound words where both elements are recognizable""",

    "zh": """\
  - Include compound words where both characters contribute meaning (this is the key feature)
  - Examples: 电话 (电=electric, 话=speech), 图书馆 (图书=books, 馆=hall), 自动车 (自=self, 动=move, 车=vehicle)
  - Include productive morpheme characters: 学, 化, 性, 者, 所, 机, 车, 力, 无, 不, 再, 新, 大, 小
  - Write in Simplified Chinese characters
  - Aim for a mix of 2-character words and 3-4 character compounds
  - Prefer words where knowing the individual characters helps understand the compound""",

    "eo": """\
  - Include words with multiple productive Esperanto affixes: mal-, re-, ek-, dis-, ge-, -eg, -et, -ul, -ej, -ist, -in, -ad, -aĵ, -ec, -ebl, -ig, -iĝ, -an, -ar
  - Include compound words where both roots are recognizable (e.g. lernejo = lern+ej+o, malsanulejo = mal+san+ul+ej+o)
  - Mix all word classes: nouns (-o), adjectives (-a), verbs (-i), adverbs (-e)
  - AVOID simple loanwords identical or near-identical to English (hotelo, banko, muziko, telefono, restoracio) — zero game value if both languages look the same
  - Prefer words where the Esperanto structure clearly reveals meaning: malsanulejo, instruisto, ebleco, malvarma, sendependa
  - Include some longer words (4+ morphemes) to showcase Esperanto's agglutinative beauty""",

    "hi": """\
  - Include words with Sanskrit prefixes (उपसर्ग): अ-, अति-, प्र-, वि-, सु-, स्व-, नि-, परि-, अनु-, सम्-
  - Include words with Persian/Arabic prefixes: बद-, बे-, ना-, ला-, हम-
  - Include words with productive suffixes: -ता, -त्व, -पन, -आई, -कार, -दार, -वाला/-वाली, -हीन, -मान/-वान
  - AVOID English loanwords used directly in Hindi (डॉक्टर, स्कूल, बस, ट्रेन, पुलिस, ऑफिस) — no morpheme value
  - Write in Devanagari script
  - Prefer words of 2-4 meaningful components where knowing the parts (prefix+root or root+suffix) helps understand the whole
  - Good examples: स्वतंत्रता, बेरोज़गार, दुकानदार, बुद्धिमान, पढ़ाई, विकास, सहयोग, नाकाम""",

    "sw": """\
  - Include nouns across major noun classes: m-/wa- (people), ki-/vi- (things/tools), m-/mi- (plants/objects), u- (abstract), ma- (plural/mass), ku- (verbal noun)
  - Include verb-derived nouns: m- + verb root (agent noun, e.g. msomaji = reader), u- + verb root (abstract, e.g. upendo = love)
  - Include words with the locative suffix -ni (e.g. nyumbani, shuleni, mjini, baharini)
  - Include verb infinitives (ku-) for common, learnable actions
  - Include words with verb extensions: -isha/-esha (causative), -ana (reciprocal), -ika/-eka (stative)
  - AVOID English loanwords (skuli, hospitali, gari, kompyuta, televisheni) — unless they have interesting class morphology worth showing
  - Prefer words where the class prefix or derivational suffix gives genuine insight into the meaning""",
}

# Language-specific notes for en-XX word lists (English words, home language = XX).
# Key concern: avoid cognates/near-identical borrowings between English and the home language.
EN_LANGUAGE_NOTES = {
    "eo": """\
  - Choose English words whose Esperanto translation uses DIFFERENT vocabulary or structure
  - AVOID near-cognates where English and Esperanto look the same: organization/organizo, communication/komunikado, music/muziko, hotel/hotelo, telephone/telefono
  - Focus on: Germanic compound words (understand, breakthrough, overlook, shortcut), and Latin-prefix words where the Esperanto equivalent is a structurally distinct root
  - Include words with clear Latin/Greek morphemes: pre-, re-, trans-, inter-, com-/con-, -tion, -ity, -ment, -ness, -ful, -less, -able, -ive
  - Mix nouns, verbs, adjectives — aim for words an intermediate learner would genuinely encounter""",

    "hi": """\
  - Choose English words whose Hindi translation is a genuinely different word — NOT an English loanword used in Hindi
  - AVOID words already used as loanwords in Hindi: doctor, school, bus, train, station, police, office, college, hospital, ticket
  - Focus on words where the Latin/Greek morpheme breakdown is illuminating: independence = in+depend+ence, impossible = im+possib+le, transportation = trans+port+ation
  - Ideal candidates: words where the Hindi equivalent uses Sanskrit morphology that parallels the English Latin/Greek — this double insight is especially valuable for Hindi speakers
  - Good candidates: independence, impossible, friendship, understanding, government, knowledge, progress, freedom, responsibility
  - Mix nouns, verbs (infinitives), adjectives""",

    "sw": """\
  - Choose English words whose Swahili translation is a native Bantu word — NOT an English loanword
  - AVOID words that are English loanwords in Swahili: school/skuli, hospital/hospitali, television/televisheni, computer/kompyuta, bus/basi, police/polisi
  - Focus on words with clear Latin/Greek or Germanic morphemes where the Swahili equivalent is structurally different (freedom → uhuru, friendship → urafiki, teacher → mwalimu)
  - Include words with productive affixes: pre-, re-, un-, inter-, -tion, -ness, -ful, -less, -able, -er/-or
  - Good candidates: friendship, freedom, understanding, independence, impossible, beautiful, powerful, knowledge, responsibility, leadership
  - Mix nouns, verbs (infinitives), adjectives""",
}

PROMPTS_DIR = Path(__file__).parent / "prompts"

SYSTEM_PROMPT = """\
You are helping build a morpheme-based language learning word puzzle game. \
Your task is to suggest words in a target language that have clear, decomposable morpheme structure \
— words where knowing the parts (prefixes, suffixes, roots, or compound elements) genuinely \
helps a learner understand the word's meaning. Quality over quantity: every word should earn its place."""


def load_existing_db_words(code: str, conn=None) -> list:
    """Return word IDs already in the DB for {code}-en, if DB is available."""
    if conn is None:
        return []
    try:
        from morpheme_db import get_done_ids
        done = get_done_ids(conn, code, "en")
        return list(done)
    except Exception:
        return []


def load_glossary_morphemes(pair: str) -> list:
    """Return morpheme keys from prompts/{pair}/glossary.txt, if it exists."""
    glossary_path = PROMPTS_DIR / pair / "glossary.txt"
    if not glossary_path.exists():
        return []
    morphemes = []
    for line in glossary_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key = line.split("|")[0].strip()
        if key:
            morphemes.append(key)
    return morphemes


def make_user_prompt(
    language: str, code: str, count: int,
    morphemes: list, existing_words: list,
    reverse: bool = False,
) -> str:
    if reverse:
        notes = EN_LANGUAGE_NOTES.get(
            code,
            "  - Focus on English words with clear Latin/Greek/Germanic morpheme structure\n"
            "  - Avoid cognates or loanwords that look identical in both languages",
        )
        target_language = "English"
    else:
        notes = LANGUAGE_NOTES.get(code, "  - Focus on words with clear prefix/suffix/compound structure")
        target_language = language

    glossary_section = ""
    if morphemes:
        morpheme_list = "  " + ", ".join(morphemes)
        glossary_section = f"""
Glossary hint — these morphemes already have detailed explanations prepared. \
Prefer words that use them, so learners get the full benefit:
{morpheme_list}
"""

    existing_section = ""
    if existing_words:
        existing_section = f"""
The following words are already in the dictionary — do not suggest them again:
  {", ".join(existing_words)}
"""

    home_note = f" (glosses will be in {language})" if reverse else ""

    if reverse:
        morpheme_requirement = """\
- ONLY include words with 2 or more distinct morphemes (prefix+root, root+suffix, or compound parts)
- DO NOT include: simple function words, monosyllabic words, or words with no decomposable structure
- Examples of words to EXCLUDE: yet, still, even, just, only, but, though, very, quite, both, such
- Examples of words to INCLUDE: friendship (friend+ship), impossible (im+possib+le), understanding (under+stand+ing), transportation (trans+port+ation)"""
    else:
        morpheme_requirement = """\
- Each word must have clear morpheme structure that can be broken down and explained (2+ parts)
- Avoid idioms, frozen expressions, or words where the etymology is completely opaque"""

    return f"""\
Generate a list of {count} {target_language} words for a morpheme-based language learning game{home_note}.

Requirements:
- Common, useful words that intermediate learners would encounter
{morpheme_requirement}
- Mix of word classes: nouns, verbs, adjectives — avoid pure function words (conjunctions, particles)
- Prefer words where the morpheme breakdown reveals or reinforces meaning

Language-specific guidance:
{notes}
{glossary_section}{existing_section}
Return ONLY the words, one per line, no numbering, no explanations, no headers or footers.
Just the {count} words."""


def main():
    parser = argparse.ArgumentParser(
        description="Generate word lists for XX-en or en-XX dictionaries"
    )
    parser.add_argument("--api-key", default=None)
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Model to use (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--count", type=int, default=DEFAULT_COUNT,
        help=f"Words per language (default: {DEFAULT_COUNT})"
    )
    parser.add_argument(
        "--lang", nargs="+", metavar="CODE",
        help="Language codes to generate (default: all). E.g. --lang de fr zh"
    )
    parser.add_argument(
        "--reverse", action="store_true",
        help="Generate en-XX word lists (English words, home lang = XX) instead of XX-en"
    )
    parser.add_argument("--db", default=None, help="Path to DB file")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("MORPHEME_SORT_ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = os.environ["MORPHEME_SORT_ANTHROPIC_API_KEY"]

    # Open DB if available (gracefully skip if DB doesn't exist yet)
    conn = None
    try:
        from morpheme_db import get_db
        conn = get_db(args.db)
    except Exception:
        pass

    client = anthropic.Anthropic(api_key=args.api_key)
    WORD_LISTS_DIR.mkdir(exist_ok=True)

    targets = {k: v for k, v in LANGUAGES.items() if not args.lang or k in args.lang}
    if args.lang:
        unknown = set(args.lang) - set(LANGUAGES)
        if unknown:
            print(f"Unknown language codes: {unknown}")
            sys.exit(1)

    direction = "en-XX" if args.reverse else "XX-en"
    print(f"Generating {direction} word lists for {len(targets)} languages ({args.count} words each)...\n")

    for code, language in targets.items():
        if args.reverse:
            target_lang, home_lang = "en", code
            out_path = WORD_LISTS_DIR / f"en-{code}-words.txt"
            pair = f"en-{code}"
            label = f"English for {language} speakers (en-{code})"
        else:
            target_lang, home_lang = code, "en"
            out_path = WORD_LISTS_DIR / f"{code}-en-words.txt"
            pair = f"{code}-en"
            label = f"{language} ({code}-en)"

        existing_words = load_existing_db_words(code, conn) if not args.reverse else []
        morphemes = load_glossary_morphemes(pair)
        notes = []
        if existing_words:
            notes.append(f"{len(existing_words)} already in DB")
        if morphemes:
            notes.append(f"{len(morphemes)} glossary morphemes")
        note_str = f" ({', '.join(notes)})" if notes else ""
        print(f"  {label}{note_str}...", end=" ", flush=True)

        user_prompt = make_user_prompt(
            language, code, args.count, morphemes, existing_words,
            reverse=args.reverse,
        )

        for attempt in range(1, 4):
            try:
                response = client.messages.create(
                    model=args.model,
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                text = response.content[0].text.strip()
                new_words = [w.strip() for w in text.splitlines() if w.strip()]
                lines = [f"# {label} morpheme dictionary word list"]
                if existing_words:
                    lines.append(f"# {len(existing_words)} words already in DB (kept for resume)")
                    lines.extend(existing_words)
                    lines.append(f"# {len(new_words)} new words")
                lines.extend(new_words)
                out_path.write_text("\n".join(lines) + "\n")
                print(f"done ({len(new_words)} new + {len(existing_words)} existing -> {out_path.name})")

                # Insert new words into DB wordlist tracker
                if conn is not None:
                    try:
                        for word in new_words:
                            conn.execute(
                                """INSERT OR IGNORE INTO wordlist_words
                                   (target_lang, home_lang, word, status, source_file)
                                   VALUES (?, ?, ?, 'pending', ?)""",
                                (target_lang, home_lang, word, out_path.name),
                            )
                        conn.commit()
                    except Exception as e:
                        print(f"  Warning: could not insert words into DB: {e}")
                break

            except anthropic.RateLimitError:
                wait = 20 * attempt
                print(f"RATE LIMIT (attempt {attempt}/3), waiting {wait}s...")
                time.sleep(wait)
                if attempt == 3:
                    print("giving up on this language.")

            except anthropic.APIError as e:
                print(f"API ERROR: {e}")
                sys.exit(1)

        time.sleep(0.5)

    if conn is not None:
        conn.close()

    print("\nDone. Review the word lists, then run generate_claude.py for each language.")
    if args.reverse:
        print("Example:")
        print("  python generate_claude.py \\")
        print("    --input word-lists/en-eo-words.txt \\")
        print("    --target English --home Esperanto \\")
        print('    --api-key "$MORPHEME_SORT_ANTHROPIC_API_KEY"')
    else:
        print("Example:")
        print("  python generate_claude.py \\")
        print("    --input word-lists/de-en-words.txt \\")
        print("    --target German --home English \\")
        print('    --api-key "$MORPHEME_SORT_ANTHROPIC_API_KEY"')


if __name__ == "__main__":
    main()
