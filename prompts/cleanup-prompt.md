# Dictionary Cleanup Prompt

## Task

You are reviewing a morpheme dictionary for a language-learning word puzzle game. Go through each word entry one at a time and check for issues. For each word, present it to me and flag any problems. Wait for my approval or edits before moving
to the next word.

## Dictionary file

`<FILENAME>`

## What to check for each word

1. **Morpheme splits**: Are the parts split at real morpheme boundaries? (e.g., "ausgehen" → "aus" + "gehen" is correct, but "au" + "sgehen" would be wrong)
2. **homeLang translations**: Is each morpheme's `homeLang` value a clear, concise English gloss? It should be 1-3 words that convey the morpheme's core meaning. Avoid overly literal or overly loose translations.
3. **translation**: Is the full-word translation accurate and natural-sounding?
4. **exampleSentence**: Is the example sentence natural and grammatically correct in the target language?
5. **exampleTranslation**: Does the English translation of the example match?
6. **Duplicates**: Flag if this word appears to duplicate another entry (same `id` or same morpheme combination).
7. **Game fit**: The game displays morphemes as blocks in a tube (2-4 parts per word). Flag words with only 1 part (too easy) or 5+ parts (won't fit).

## Output format

For each word, show:

```
### Word N: <id>
Parts: <targetLang1> (<homeLang1>) + <targetLang2> (<homeLang2>) + ...
Translation: <translation>
Example: <exampleSentence> — <exampleTranslation>

Issues: <list issues, or "None — looks good">
```

Then wait for me to say one of:
- **ok** — keep as-is, move to next
- **edit** — I'll provide corrections, then apply them and move to next
- **delete** — remove this entry
- **skip to N** — jump ahead to word N

After all words are reviewed, output the cleaned-up JSON file.

## Sample JSON file
{
  "words": [
    {
      "id": "kindergarten",
      "gender": "m",
      "parts": [
        {"targetLang": "kinder", "homeLang": "children"},
        {"targetLang": "garten", "homeLang": "garden"}
      ],
      "translationShort": "kindergarten",
      "translationLong": "",
      "literalMeaning": "children's garden",
      "exampleSentence": "Mein Sohn geht in den Kindergarten.",
      "exampleTranslation": "My son goes to kindergarten."
    },
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
    },
    {
      "id": "einkaufen",
      "gender": "",
      "parts": [
        {"targetLang": "ein", "homeLang": "in-"},
        {"targetLang": "kauf", "homeLang": "buy"},
        {"targetLang": "en", "homeLang": "(infinitive)"}
      ],
      "translationShort": "to go shopping",
      "translationLong": "to go shopping, to buy (something)",
      "literalMeaning": "to trade in",
      "exampleSentence": "Er kauft oft ein.",
      "exampleTranslation": "He shops often."
    }
  ]
}
