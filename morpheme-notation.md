# Morpheme homeLang Notation Guide

This is a guide for writing `homeLang` tile labels, not an exhaustive ruleset. When in doubt: would a player immediately understand what this part contributes to the word's meaning? If yes, it's good.

## Core Rules

1. **Pick one gloss only** — no slashes. If there are meaningful alternatives, put them in `homeLangDetails`.
2. **Parentheses = grammatical marker** — never use parentheses for semantic content. For the app's hide-grammatical-markers setting, parentheses in the label is one signal; `morpheme_type` or a dedicated field may also be used.
3. **When plain accuracy would mislead**, keep the tile short anyway and use `homeLangDetails` to explain.

## Notation by Type

| Type | Notation | Example |
|------|----------|---------|
| Semantic root | plain word | `reach`, `spirit`, `forest` |
| Derivational prefix | plain word or dash notation | `completion`, `away`, `one`, `re-` |
| Derivational affix with English equivalent | dash notation | `-able`, `-ness`, `un-`, `-less` |
| Grammatical inflection with English equivalent | dash notation | `-ung` → `-ing`, `ge-` → `-ed`, `-en` (pl) → `-s` |
| Grammatical inflection with no English equivalent | parenthetical label | `-en` (inf) → `(verb)` |
| Fugenlaut / linking element | `-` | `s`, `e` between compound parts |

## Language-Specific Grammatical Labels

Labels should be plain words players already know, not linguistic jargon. Understandable in context, not too abbreviated.

### German
| Morpheme | Situation | Label |
|----------|-----------|-------|
| `-en` | infinitive | `(verb)` |
| `ge-` | past participle | `-ed` |
| `-en` | plural | `-s` |
| `-er` | comparative | `-er` |

### Turkish
| Morpheme | Situation | Label |
|----------|-----------|-------|
| `-da/-de` | locative ("in/at") | `(in)` |
| `-dan/-den` | ablative ("from") | `(from)` |
| `-a/-e` | dative ("to") | `(to)` |
| `-ı/-i/-u/-ü` | accusative (direct object) | `(object)` |
| `-lar/-ler` | plural | `-s` |

### Japanese
| Morpheme | Situation | Label |
|----------|-----------|-------|
| `は` | topic marker | `(topic)` |
| `が` | subject marker | `(subject)` |
| `を` | object marker | `(object)` |
| `の` | possessive / nominalizer | `(of)` |
| `た` | past tense | `-ed` |

### Russian
| Morpheme | Situation | Label |
|----------|-----------|-------|
| genitive ending | "of" / possession | `(of)` |
| dative ending | "to" / indirect object | `(to)` |
| accusative ending | direct object | `(object)` |
| instrumental ending | "with" / by means of | `(with)` |

## Open Questions

- Case endings in Russian/Turkish: are `(in)`, `(from)`, `(to)` plain enough or do they read as semantic?
- Swahili noun class prefixes (`m-`/`wa-`, `ki-`/`vi-`) — no English equivalent, what's the label?
- Arabic vowel patterns marking tense/person — may not be separable morphemes in the usual sense
- Whether `-t` (German 3rd person present), `-er` (comparative) etc. need entries here
