#!/usr/bin/env python3
"""
Dictionary generator for the morpheme word puzzle game.

Reads a text file of words (one per line) and generates a JSON dictionary
with morpheme splits, translations, and example sentences.

Usage:
    python generate_dictionary.py --lang de --input words.txt --output dict.json
    python generate_dictionary.py --lang ru --input words.txt --output dict.json --google-api-key KEY
    python generate_dictionary.py --lang ja --input words.txt --output dict.json --no-examples
"""

import argparse
import json
import os
import pickle
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Language registry — maps ISO 639-1 to splitter class + config
# ---------------------------------------------------------------------------

LANG_CONFIG = {
    "de": {
        "name": "German",
        "iso3": "deu",
        "spacy_model": "de_core_news_md",
        "splitter": "german",
        "has_gender": True,
        "gender_map": {"Masc": "der", "Fem": "die", "Neut": "das"},
    },
    "nl": {
        "name": "Dutch",
        "iso3": "nld",
        "spacy_model": "nl_core_news_md",
        "splitter": "dutch",
        "has_gender": True,
        "gender_map": {"Com": "de", "Neut": "het"},
    },
    "ru": {
        "name": "Russian",
        "iso3": "rus",
        "spacy_model": "ru_core_news_md",
        "splitter": "russian",
        "has_gender": False,
    },
    "es": {
        "name": "Spanish",
        "iso3": "spa",
        "spacy_model": "es_core_news_md",
        "splitter": "spanish",
        "has_gender": True,
        "gender_map": {"Masc": "el", "Fem": "la"},
    },
    "fr": {
        "name": "French",
        "iso3": "fra",
        "spacy_model": "fr_core_news_md",
        "splitter": "french",
        "has_gender": True,
        "gender_map": {"Masc": "le", "Fem": "la"},
    },
    "ja": {
        "name": "Japanese",
        "iso3": "jpn",
        "spacy_model": "ja_core_news_md",
        "splitter": "japanese",
        "has_gender": False,
    },
    "zh": {
        "name": "Chinese",
        "iso3": "cmn",
        "spacy_model": "zh_core_web_md",
        "splitter": "chinese",
        "has_gender": False,
    },
}

# ---------------------------------------------------------------------------
# Morpheme splitters
# ---------------------------------------------------------------------------


def _char_split_once(word: str, min_part_len: int = 3) -> tuple[str, str] | None:
    """Try to split a word into exactly two parts using char_split.

    Returns (left, right) if a good split is found, None otherwise.
    """
    from compound_split import char_split

    if len(word) < min_part_len * 2:
        return None

    results = char_split.split_compound(word)
    if not results:
        return None

    score, left, right = results[0]

    # Only accept splits with a reasonable score and both parts long enough.
    # Require score > -0.3 (higher = more confident split).
    if score < -0.3 or len(left) < min_part_len or len(right) < min_part_len:
        return None

    return left, right


def _char_split_recursive(word: str, min_part_len: int = 3, depth: int = 0) -> list[str]:
    """Recursively split a German/Dutch compound using char_split.

    Uses the character n-gram model which handles more words than doc_split.
    Recurses on each part to find deeper splits (e.g., Verbildlichen → Ver + Bild + lichen).
    """
    if depth > 2:
        return [word]

    result = _char_split_once(word, min_part_len)
    if result is None:
        return [word]

    left, right = result

    # Only recurse on parts that are long enough to plausibly be compounds themselves.
    # Most German morphemes are 3-7 chars, so only recurse on 8+ char parts.
    recurse_min = 8
    if len(left) >= recurse_min:
        left_parts = _char_split_recursive(left, min_part_len, depth + 1)
    else:
        left_parts = [left]

    if len(right) >= recurse_min:
        right_parts = _char_split_recursive(right, min_part_len, depth + 1)
    else:
        right_parts = [right]

    return left_parts + right_parts


# German connecting elements (Fugenlaute) — only the single-char ones are safe
# to auto-strip. Multi-char ones like "en", "er" are too often part of the stem.
_FUGEN_SAFE = {"s"}


def _strip_fugen(part: str) -> tuple[str, str]:
    """Strip a trailing Fugenlaut (connecting element) from a compound part.

    Returns (stem, fugenlaut) — e.g., ("geburt", "s") from "geburts".
    Only strips single-char connectors ('s') to avoid false positives
    like "wasser" → "wass" + "er" or "kinder" → "kind" + "er".
    """
    lower = part.lower()
    for f in _FUGEN_SAFE:
        if lower.endswith(f) and len(lower) - len(f) >= 3:
            return lower[: -len(f)], f
    return lower, ""


def split_german(word: str) -> list[str]:
    """Split a German compound word into morphemes.

    Uses char_split (character n-gram model) with recursive splitting,
    then detects and separates connecting elements (Fugenlaute).
    """
    try:
        from compound_split import doc_split

        # Try doc_split first (dictionary-based, more reliable when it works)
        parts = doc_split.maximal_split(word)
        if parts and len(parts) > 1:
            result = [p.lower() for p in parts]
        else:
            # Fall back to char_split with recursive splitting
            result = [p.lower() for p in _char_split_recursive(word)]

        if len(result) <= 1:
            return [word.lower()]

        # Split off Fugenlaute (connecting elements) from intermediate parts
        # e.g., ["geburts", "tag"] → ["geburt", "s", "tag"]
        expanded = []
        for i, part in enumerate(result):
            if i < len(result) - 1:  # not the last part
                stem, fugen = _strip_fugen(part)
                expanded.append(stem)
                if fugen:
                    expanded.append(fugen)
            else:
                expanded.append(part)

        return expanded

    except ImportError:
        print("Warning: compound-split not installed, falling back to no split")
    except Exception as e:
        print(f"Warning: compound-split failed for '{word}': {e}")
    return [word.lower()]


def split_dutch(word: str) -> list[str]:
    """Split a Dutch compound word using compound-split."""
    # compound-split supports Dutch too, same approach
    return split_german(word)


def split_morfessor(word: str, model) -> list[str]:
    """Split a word using a Morfessor model."""
    if model is None:
        return [word.lower()]
    try:
        segments, _ = model.viterbi_segment(word.lower())
        return segments
    except Exception as e:
        print(f"Warning: Morfessor failed for '{word}': {e}")
        return [word.lower()]


# ---------------------------------------------------------------------------
# Rule-based prefix/suffix splitting for languages without compound-split
# ---------------------------------------------------------------------------

# Russian prefixes (sorted longest-first to match greedily)
_RU_PREFIXES = sorted([
    "без", "бес", "в", "во", "воз", "вос", "вы", "до", "за", "из", "ис",
    "на", "над", "не", "недо", "о", "об", "обо", "от", "ото", "пере",
    "по", "под", "подо", "пред", "при", "про", "раз", "рас", "с", "со",
    "у", "через", "черес",
], key=len, reverse=True)

# Russian suffixes (verb/noun/adj endings, sorted longest-first)
_RU_SUFFIXES = sorted([
    "ться", "ить", "ать", "еть", "уть", "ять", "оть", "ти",
    "ный", "ной", "ная", "ное", "ние", "ица", "ость", "ство",
    "тель", "ник", "чик", "щик", "ка", "ок", "ек",
], key=len, reverse=True)

# Spanish prefixes
_ES_PREFIXES = sorted([
    "des", "in", "im", "ir", "re", "pre", "sobre", "sub", "contra",
    "entre", "para", "anti", "auto", "bi", "co", "ex", "extra",
    "inter", "multi", "pos", "semi", "super", "trans", "ultra",
], key=len, reverse=True)

_ES_SUFFIXES = sorted([
    "ción", "sión", "mente", "ible", "able", "idad", "ismo", "ista",
    "ero", "era", "oso", "osa", "izar", "ar", "er", "ir",
], key=len, reverse=True)

# French prefixes
_FR_PREFIXES = sorted([
    "dé", "des", "dis", "in", "im", "ir", "il", "re", "ré",
    "pré", "sur", "sous", "contre", "entre", "anti", "auto",
    "bi", "co", "ex", "extra", "inter", "multi", "para",
    "semi", "super", "trans", "ultra",
], key=len, reverse=True)

_FR_SUFFIXES = sorted([
    "tion", "sion", "ment", "ible", "able", "ité", "isme", "iste",
    "eur", "euse", "eux", "euse", "iser", "er", "ir", "re",
], key=len, reverse=True)


def split_by_affixes(word: str, prefixes: list[str], suffixes: list[str], min_root: int = 2) -> list[str]:
    """Split a word by stripping known prefixes and suffixes.

    Returns a list of morphemes: [prefix, root, suffix] (any may be absent).
    Only splits if the remaining root is at least min_root characters.
    """
    lower = word.lower()
    parts = []

    # Try to strip a prefix
    prefix_found = ""
    for prefix in prefixes:
        if lower.startswith(prefix) and len(lower) - len(prefix) >= min_root:
            prefix_found = prefix
            lower = lower[len(prefix):]
            break

    # Try to strip a suffix
    suffix_found = ""
    for suffix in suffixes:
        if lower.endswith(suffix) and len(lower) - len(suffix) >= min_root:
            suffix_found = suffix
            lower = lower[: -len(suffix)]
            break

    if prefix_found:
        parts.append(prefix_found)
    parts.append(lower)  # root
    if suffix_found:
        parts.append(suffix_found)

    # Only return the split if we actually found something
    if len(parts) > 1:
        return parts
    return [word.lower()]


def split_russian(word: str) -> list[str]:
    """Split a Russian word using prefix/suffix rules."""
    return split_by_affixes(word, _RU_PREFIXES, _RU_SUFFIXES)


def split_spanish(word: str) -> list[str]:
    """Split a Spanish word using prefix/suffix rules."""
    return split_by_affixes(word, _ES_PREFIXES, _ES_SUFFIXES)


def split_french(word: str) -> list[str]:
    """Split a French word using prefix/suffix rules."""
    return split_by_affixes(word, _FR_PREFIXES, _FR_SUFFIXES)


_ja_tagger = None


def split_japanese(word: str, spacy_nlp=None) -> list[str]:
    """Split a Japanese word into morphemes.

    Tries fugashi (MeCab) first, falls back to spaCy's Japanese tokenizer
    (which uses SudachiPy internally).
    """
    global _ja_tagger

    # Try fugashi first
    if _ja_tagger is None:
        try:
            from fugashi import Tagger
            _ja_tagger = Tagger()
        except Exception:
            _ja_tagger = "unavailable"

    if _ja_tagger != "unavailable":
        tokens = [w.surface for w in _ja_tagger(word) if w.surface.strip()]
        if tokens:
            return tokens

    # Fall back to spaCy Japanese tokenizer
    if spacy_nlp is not None:
        doc = spacy_nlp(word)
        tokens = [token.text for token in doc if token.text.strip()]
        if tokens and len(tokens) > 1:
            return tokens

    # For single-token words, split into individual characters (each kanji is a morpheme)
    if len(word) >= 2:
        return list(word)

    return [word]


def split_chinese(word: str) -> list[str]:
    """Split a Chinese word/phrase using jieba.

    For short words (2-4 chars), splits into individual characters since
    each Chinese character is typically a morpheme.
    For longer phrases, uses jieba to find word boundaries first.
    """
    # For short words, character-level split is more useful for the game
    # (each character is a morpheme: 电脑 = 电 electric + 脑 brain)
    if len(word) <= 4:
        return list(word)

    try:
        import jieba

        tokens = list(jieba.cut(word, cut_all=False))
        # If jieba returns a single token, fall back to character split
        if len(tokens) <= 1:
            return list(word)
        return tokens
    except ImportError:
        return list(word)


# ---------------------------------------------------------------------------
# Morfessor model management
# ---------------------------------------------------------------------------


def get_morfessor_model(lang: str, spacy_nlp) -> "morfessor.BaselineModel | None":
    """Train or load a cached Morfessor model for the given language."""
    cache_dir = Path(__file__).parent / ".cache"
    cache_dir.mkdir(exist_ok=True)
    cache_path = cache_dir / f"morfessor_{lang}.bin"

    try:
        import morfessor
    except ImportError:
        print("Warning: Morfessor not installed")
        return None

    if cache_path.exists():
        print(f"Loading cached Morfessor model for {lang}...")
        io = morfessor.MorfessorIO()
        return io.read_binary_model_file(str(cache_path))

    print(f"Training Morfessor model for {lang} from spaCy vocab (this may take a minute)...")
    model = morfessor.BaselineModel()

    # Build training data from spaCy's vocabulary
    training_data = []
    for word in spacy_nlp.vocab:
        text = word.text.lower()
        if text.isalpha() and len(text) > 2:
            freq = max(1, int(word.prob * -1))  # spaCy prob is log, approximate freq
            training_data.append((freq, text))

    if not training_data:
        # Fallback: use a simple word list
        print("Warning: spaCy vocab empty, Morfessor model will be untrained")
        return model

    model.load_data(training_data[:50000])  # cap to avoid long training
    model.train_batch()

    # Cache the model
    io = morfessor.MorfessorIO()
    io.write_binary_model_file(str(cache_path), model)
    print(f"Morfessor model cached to {cache_path}")

    return model


# ---------------------------------------------------------------------------
# Article detection
# ---------------------------------------------------------------------------


def get_gender(word: str, lang_config: dict, spacy_nlp) -> str:
    """Get the gender/article for a noun using spaCy's morphological analysis."""
    if not lang_config.get("has_gender"):
        return ""

    gender_map = lang_config.get("gender_map", {})
    if not gender_map:
        return ""

    doc = spacy_nlp(word)
    for token in doc:
        if token.pos_ == "NOUN":
            genders = token.morph.get("Gender")
            if genders:
                return gender_map.get(genders[0], "")

    return ""


# ---------------------------------------------------------------------------
# Google Translate (with disk cache)
# ---------------------------------------------------------------------------

_translate_cache: dict[str, str] = {}
_translate_cache_path: Path | None = None


def _load_translate_cache(source_lang: str):
    """Load translation cache from disk."""
    global _translate_cache, _translate_cache_path
    cache_dir = Path(__file__).parent / ".cache"
    cache_dir.mkdir(exist_ok=True)
    _translate_cache_path = cache_dir / f"translate_{source_lang}_en.pkl"

    if _translate_cache_path.exists():
        try:
            with open(_translate_cache_path, "rb") as f:
                _translate_cache = pickle.load(f)
            print(f"Loaded {len(_translate_cache)} cached translations")
        except Exception:
            _translate_cache = {}


def _save_translate_cache():
    """Persist translation cache to disk."""
    if _translate_cache_path:
        try:
            with open(_translate_cache_path, "wb") as f:
                pickle.dump(_translate_cache, f)
        except Exception:
            pass


def translate_text(text: str, source_lang: str, target_lang: str = "en", api_key: str = None) -> str:
    """Translate text using Google Cloud Translation API v2. Results are cached."""
    if not api_key:
        return "?"

    # Check cache first
    cache_key = f"{text}|{source_lang}|{target_lang}"
    if cache_key in _translate_cache:
        return _translate_cache[cache_key]

    try:
        import requests

        response = requests.get(
            "https://translation.googleapis.com/language/translate/v2",
            params={
                "key": api_key,
                "q": text,
                "source": source_lang,
                "target": target_lang,
            },
            timeout=10,
        )
        data = response.json()
        if "data" in data and "translations" in data["data"]:
            result = data["data"]["translations"][0]["translatedText"]
            _translate_cache[cache_key] = result
            _save_translate_cache()
            return result
        else:
            error_msg = data.get("error", {}).get("message", "Unknown error")
            print(f"Warning: Translation API error for '{text}': {error_msg}")
            return "?"
    except ImportError:
        print("Warning: requests not installed, cannot translate")
        return "?"
    except Exception as e:
        print(f"Warning: Translation failed for '{text}': {e}")
        return "?"


# ---------------------------------------------------------------------------
# Tatoeba example sentences
# ---------------------------------------------------------------------------


class TatoebaAPI:
    """Looks up example sentences via the Tatoeba REST API."""

    def __init__(self, iso3_source: str, iso3_target: str = "eng"):
        self.iso3_source = iso3_source
        self.iso3_target = iso3_target
        # Cache results to avoid repeat API calls
        self._cache: dict[str, tuple[str, str] | None] = {}

        cache_dir = Path(__file__).parent / ".cache"
        cache_dir.mkdir(exist_ok=True)
        self.cache_path = cache_dir / f"tatoeba_{iso3_source}_{iso3_target}.pkl"

        # Load cache from disk if available
        if self.cache_path.exists():
            try:
                with open(self.cache_path, "rb") as f:
                    self._cache = pickle.load(f)
                print(f"Loaded {len(self._cache)} cached Tatoeba lookups")
            except Exception:
                self._cache = {}

    def load(self):
        """No-op for API mode (kept for interface compatibility)."""
        pass

    def _save_cache(self):
        """Persist cache to disk."""
        try:
            with open(self.cache_path, "wb") as f:
                pickle.dump(self._cache, f)
        except Exception:
            pass

    def find_example(self, word: str) -> tuple[str, str] | None:
        """Find an example sentence containing the word via Tatoeba API.

        Returns (sentence, translation) or None. Results are cached.
        """
        import requests
        import time

        key = word.lower()
        if key in self._cache:
            return self._cache[key]

        try:
            response = requests.get(
                "https://tatoeba.org/en/api_v0/search",
                params={
                    "from": self.iso3_source,
                    "to": self.iso3_target,
                    "query": word,
                    "orphans": "no",
                    "trans_filter": "limit",
                    "trans_to": self.iso3_target,
                    "sort": "words",  # shorter sentences first
                },
                timeout=15,
            )
            data = response.json()
            results = data.get("results", [])

            for sentence in results:
                text = sentence.get("text", "")
                translations = sentence.get("translations", [[]])
                # translations is a list of lists; first group is direct translations
                if translations and translations[0]:
                    eng_text = translations[0][0].get("text", "")
                    if eng_text:
                        result = (text, eng_text)
                        self._cache[key] = result
                        self._save_cache()
                        # Be polite to the API
                        time.sleep(0.5)
                        return result

            # No result found
            self._cache[key] = None
            self._save_cache()
            time.sleep(0.5)
            return None

        except Exception as e:
            print(f"Warning: Tatoeba API error for '{word}': {e}")
            return None


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def generate_entry(
    word: str,
    lang: str,
    lang_config: dict,
    spacy_nlp,
    morfessor_model,
    tatoeba: TatoebaAPI | None,
    api_key: str | None,
) -> dict:
    """Generate a single dictionary entry for a word."""

    # 1. Split into morphemes
    splitter_type = lang_config["splitter"]
    if splitter_type == "german":
        parts = split_german(word)
    elif splitter_type == "dutch":
        parts = split_dutch(word)
    elif splitter_type == "russian":
        parts = split_russian(word)
    elif splitter_type == "spanish":
        parts = split_spanish(word)
    elif splitter_type == "french":
        parts = split_french(word)
    elif splitter_type == "morfessor":
        parts = split_morfessor(word, morfessor_model)
    elif splitter_type == "japanese":
        parts = split_japanese(word, spacy_nlp)
    elif splitter_type == "chinese":
        parts = split_chinese(word)
    else:
        parts = [word.lower()]

    # 2. Generate homeLang glosses via Google Translate
    morpheme_parts = []
    for part in parts:
        gloss = translate_text(part, lang, "en", api_key)
        morpheme_parts.append({"targetLang": part, "homeLang": gloss})

    # 3. Get gender (if applicable)
    gender = get_gender(word, lang_config, spacy_nlp)

    # 4. Get full-word translation
    translation_short = translate_text(word, lang, "en", api_key)

    # 5. Build literal meaning from morpheme glosses
    glosses = [p["homeLang"] for p in morpheme_parts if p["homeLang"] != "?"]
    literal_meaning = " ".join(glosses) if glosses and len(glosses) > 1 else ""

    # 6. Find example sentence
    example_sentence = ""
    example_translation = ""
    if tatoeba is not None:
        result = tatoeba.find_example(word)
        if result:
            example_sentence, example_translation = result

    return {
        "id": word.lower(),
        "gender": gender,
        "parts": morpheme_parts,
        "translationShort": translation_short,
        "translationLong": "",
        "literalMeaning": literal_meaning,
        "exampleSentence": example_sentence,
        "exampleTranslation": example_translation,
    }


def load_spacy_model(model_name: str):
    """Load a spaCy model, with a helpful error if not installed."""
    try:
        import spacy

        return spacy.load(model_name)
    except OSError:
        print(f"Error: spaCy model '{model_name}' not found.")
        print(f"Install it with: python -m spacy download {model_name}")
        sys.exit(1)


def fill_missing(existing_path: str, lang: str, lang_config: dict, api_key: str | None, skip_examples: bool):
    """Load an existing dictionary JSON and fill in any '?' values."""
    print(f"Fill mode: updating {existing_path}...")

    with open(existing_path) as f:
        data = json.load(f)

    entries = data.get("words", [])
    if not entries:
        print("No entries found in file.")
        return

    # Initialize translation cache
    _load_translate_cache(lang)

    # Load Tatoeba if needed
    tatoeba = None
    if not skip_examples:
        tatoeba = TatoebaAPI(lang_config["iso3"])

    updated = 0
    for i, entry in enumerate(entries, 1):
        word = entry["id"]
        changed = False

        # Fill missing morpheme glosses
        for part in entry.get("parts", []):
            if part.get("homeLang") == "?":
                gloss = translate_text(part["targetLang"], lang, "en", api_key)
                if gloss != "?":
                    part["homeLang"] = gloss
                    changed = True

        # Fill missing translation
        if entry.get("translationShort") == "?":
            trans = translate_text(word, lang, "en", api_key)
            if trans != "?":
                entry["translationShort"] = trans
                changed = True

        # Fill missing example sentence
        if not entry.get("exampleSentence") and tatoeba is not None:
            result = tatoeba.find_example(word)
            if result:
                entry["exampleSentence"] = result[0]
                entry["exampleTranslation"] = result[1]
                changed = True

        if changed:
            updated += 1
            print(f"[{i}/{len(entries)}] Updated: {word}")
        else:
            if i % 100 == 0:
                print(f"[{i}/{len(entries)}] (no changes needed)")

    # Write back
    Path(existing_path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"\nUpdated {updated}/{len(entries)} entries in {existing_path}")

    # Summary of remaining gaps
    no_gloss = sum(1 for e in entries for p in e["parts"] if p.get("homeLang") == "?")
    no_trans = sum(1 for e in entries if e.get("translationShort") == "?")
    no_example = sum(1 for e in entries if not e.get("exampleSentence"))
    if no_gloss:
        print(f"  {no_gloss} morphemes still need glosses")
    if no_trans:
        print(f"  {no_trans} words still need translations")
    if no_example:
        print(f"  {no_example} words still missing examples")


def main():
    parser = argparse.ArgumentParser(description="Generate morpheme dictionary from word list")
    parser.add_argument("--lang", required=True, choices=LANG_CONFIG.keys(), help="Target language (ISO 639-1)")
    parser.add_argument("--input", required=True, help="Input text file (one word per line) OR existing JSON in --fill mode")
    parser.add_argument("--output", required=True, help="Output JSON file path")
    parser.add_argument("--google-api-key", default=None, help="Google Cloud Translation API key")
    parser.add_argument("--no-examples", action="store_true", help="Skip Tatoeba example sentence lookup")
    parser.add_argument("--fill", action="store_true", help="Fill '?' values in an existing JSON (--input is the JSON file, --output is ignored)")
    args = parser.parse_args()

    lang_config = LANG_CONFIG[args.lang]

    # Initialize translation cache
    _load_translate_cache(args.lang)

    # Fill mode: update existing JSON
    if args.fill:
        fill_missing(args.input, args.lang, lang_config, args.google_api_key, args.no_examples)
        return

    print(f"Generating {lang_config['name']} dictionary...")

    # Read input words
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {args.input}")
        sys.exit(1)

    words = [line.strip() for line in input_path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    print(f"Read {len(words)} words from {args.input}")

    # Load spaCy
    print(f"Loading spaCy model '{lang_config['spacy_model']}'...")
    spacy_nlp = load_spacy_model(lang_config["spacy_model"])

    # Load Morfessor if needed
    morfessor_model = None
    if lang_config["splitter"] == "morfessor":
        morfessor_model = get_morfessor_model(args.lang, spacy_nlp)

    # Load Tatoeba
    tatoeba = None
    if not args.no_examples:
        tatoeba = TatoebaAPI(lang_config["iso3"])
        tatoeba.load()

    # Generate entries
    entries = []
    for i, word in enumerate(words, 1):
        print(f"[{i}/{len(words)}] Processing: {word}")
        entry = generate_entry(word, args.lang, lang_config, spacy_nlp, morfessor_model, tatoeba, args.google_api_key)
        entries.append(entry)

    # Write output
    output = {"words": entries}
    output_path = Path(args.output)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(f"\nWrote {len(entries)} entries to {args.output}")

    # Summary
    no_gloss = sum(1 for e in entries for p in e["parts"] if p["homeLang"] == "?")
    no_example = sum(1 for e in entries if not e["exampleSentence"])
    single_part = sum(1 for e in entries if len(e["parts"]) == 1)
    if no_gloss:
        print(f"  {no_gloss} morphemes need manual glosses (marked '?')")
    if no_example:
        print(f"  {no_example} words missing example sentences")
    if single_part:
        print(f"  {single_part} words have only 1 part (may not split well in game)")


if __name__ == "__main__":
    main()
