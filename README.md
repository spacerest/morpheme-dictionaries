# Morpheme Dictionaries

JSON dictionaries for a language-learning word puzzle game. Each entry splits a target-language word into its morphemes, with glosses, a translation, and an example sentence.

## Dictionary format

```json
{
  "words": [
    {
      "id": "geburtstag",
      "gender": "m",
      "parts": [
        {"targetLang": "geburt", "homeLang": "birth"},
        {"targetLang": "s", "homeLang": "-"},
        {"targetLang": "tag", "homeLang": "day"}
      ],
      "translationShort": "birthday",
      "translationLong": "",
      "literalMeaning": "birth day",
      "exampleSentence": "Heute ist mein Geburtstag!",
      "exampleTranslation": "Today is my birthday!"
    }
  ]
}
```

## Generating a dictionary with Claude

`generate_claude.py` batches a word list through the Claude API and writes a JSON dictionary.

### Setup

```bash
pip install anthropic
```

### Usage

```bash
python generate_claude.py \
  --input word-lists/de-en.txt \
  --output dicts/de-en.json \
  --home English \
  --target German \
  --api-key sk-ant-...
```

The `--api-key` argument is optional if the `ANTHROPIC_API_KEY` environment variable is set:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python generate_claude.py --input word-lists/de-en.txt --output dicts/de-en.json --home English --target German
```

### All options

| Flag | Default | Description |
|---|---|---|
| `--input` | (required) | Word list file, one word per line |
| `--output` | (required) | Output JSON file |
| `--home` | (required) | Home language, e.g. `English` |
| `--target` | (required) | Target language, e.g. `German` |
| `--api-key` | env var | Anthropic API key |
| `--batch-size` | `25` | Words per API call |
| `--model` | `claude-sonnet-4-6` | Claude model to use |

### Resuming interrupted runs

Progress is saved after every batch. If a run is interrupted, re-run the same command — words already present in the output file are skipped automatically.

### Flagged entries

Words with only 1 morpheme part or 5+ parts are flagged in the output (the game expects 2–4 parts per word). A summary is printed at the end of the run.

## Prompts

The Claude prompts are in `prompts/`:

- `prompts/system.txt` — system prompt sent once per session
- `prompts/user.txt` — user message template sent with each batch
- `prompts/create-dict-prompt.md` — human-readable reference for both of the above
- `prompts/cleanup-prompt.md` — interactive prompt for reviewing and editing entries one-by-one

## Files

```
generate_claude.py          Claude-based dictionary generator
generate_dictionary.py      Rule-based generator (compound-split + Google Translate + Tatoeba)
requirements.txt

dicts/
  de-en-1.json              German–English, version 1
  de-en-2.json              German–English, version 2
  de-en-regenerated.json    German–English, regenerated
  ru-en-1.json              Russian–English, version 1

word-lists/
  de-en.txt                 German word list (907 words)

prompts/
  system.txt                Claude system prompt
  user.txt                  Claude user message template
  create-dict-prompt.md     Human-readable prompt reference
  cleanup-prompt.md         Interactive review prompt
```
