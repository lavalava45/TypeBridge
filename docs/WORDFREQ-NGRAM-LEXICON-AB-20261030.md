# TypeBridge — R16 research: wordfreq vocabulary + character n-gram A/B

**Date:** 2026-10-30. **Status:** Experiment completed; **NO production
integration, no release clearance.** Pre-existing `No01` remains `BLOCKED`.

## Questions answered

1. Ready-made multilingual lexical evidence exists. `wordfreq` (Python 3.1.1)
   has RU/EN/HE frequency tables and `top_n_list` / `zipf_frequency`. These
   are corpus frequency estimates, **not a dictionary of lexicographically
   verified words**. `wordfreq` code is Apache-2.0; frequency data is
   CC BY-SA 4.0 and has additional source notices and attribution duties.
2. For Wiktionary, the public `kaikki.org/dictionary/` service publishes
   structured machine-readable datasets from Wiktextract for RU/EN/HE,
   including part-of-speech, inflection and proper-name records. **The bulk
   Wiktionary datasets were examined for availability/licensing, not imported
   in this experiment.** Their full size, POS filtering, inflection quality,
   proper-name provenance and share-alike obligations need a separate ingest
   trial before TypeBridge redistributes derived files.
3. Pretrained fastText language-ID models cover RU/EN/HE using character
   features. The historic 176-language `lid.176.ftz` is compressed (~917 kB)
   and licensed CC BY-SA 3.0; new NLLB `lid218e` is CC BY-NC 4.0. Those are
   **language classifiers for text**, not safe single-token keyboard-layout
   correction models. Neither was included as an executable dependency here.
4. A small word-type character 3/4/5-gram model was trained locally from
   `wordfreq.top_n_list(lang, 30000)` (unweighted distinct word types);
   its effect was compared with the current real R16 v2 decision evaluator.

## Isolation, exact source, repeatability

Source-only script: `experiments/lexicon_ngram_ab_20261030.py`.
Research-only tests: `experiments/test_lexicon_ngram_ab_20261030.py`.

Pinned third-party dependency `wordfreq==3.1.1` and its imports reside in
`build/_spellcheck_research_deps/`; `pip --target` was used instead of a
project dependency change. No TypeBridge working Python source, public or
diagnostic EXE, language pack, user dictionary, profile or Windows input
setting was edited. No physical input or global hooks were touched.

Experiment evaluates production `PolicyDecisionEngine.decide_token` with
the independently defined **synthetic** three-keyboard mapper, bundled v2
language metadata and current source-only read of `data/`. It never
instantiates `LayoutMapper`, Windows keyboard APIs or TypeBridge live GUI.

Frozen independent Wikipedia titles:
`experiments/fixtures/windows_spellcheck_wikipedia_titles_20261030.json`.
Only 185 previously verified OOV titles were eligible. **60** were put in
the experimental Wikipedia proper-name *training* set (RU 11/EN 29/HE 20);
the remaining **125** were held out (RU 34/EN 51/HE 40), yielding 250
wrong-layout positive examples. This split is by pageid modulo 3, fixed
before testing. A Wikipedia title is evidence of an attested encyclopedia
entry, not proof that a term belongs in a confident word dictionary.

From `wordfreq` top-ranked words, selected **1000 existing-dictionary OOV**
word types per language (3,000 total), requiring single script,
4–16 letters and compatibility with the synthetic key-map. Each of the
three candidate sets is followed by a separate **200 OOV word type per
language held-out set** (600 sources, 1,200 wrong-layout examples); these
were **not added** to any tested dictionary. Wordfreq-derived strings are
not independently certified by Wiktionary, and are not neutral examples of
how often real users type a word.

| Language | Added OOV | Last rank inspected incl. 200 holdouts | Added with Zipf >=3 |
|---|---:|---:|---:|
| RU | 1,000 | 77,548 | 756 |
| EN | 1,000 | 56,447 | 73 |
| HE | 1,000 | 49,729 | 1,000 |

The same numeric frequency threshold yields very unequal confidence
coverage across languages. Relative Zipf scales and how data were derived
must be calibrated before use as an automatic-switch criterion.

## Baseline and experimental tiers

- **Baseline:** unchanged production words/confident sets.
- **Protect only:** add 3,000 OOV frequency words and 60 Wikipedia proper
  names as ordinary `words`, never as `confident`.
- **All confident:** add all those word types as both `words` and `confident`.
- **Frequency confident:** all are added to `words`, but only the wordfreq
  types above a fixed Zipf threshold (3.0) become `confident`.
- For each tier, an optional **n-gram veto** rejects an otherwise proposed
  change if target character-model score minus source score is below a
  margin. This is a **veto**, not permission to add unknown candidates.

The experiment reuses previously independent random noise and abbreviation
controls, synthetic typing errors and attested OOV titles. It also adds
other-seed baseline confident positives and frequency holdouts. **4,528**
synthetic scenarios were tested, not representative of real-user priors.

## A: n-gram results

The score is smoothed average log-likelihood of character **4-grams**
(3/5-gram counters also created for exploration). Word-types were not
frequency weighted. Margin `0.5` was selected on a **different calibration
sample**, subject to retaining at least 97% of existing positive decisions;
the calibration retained 234/240 (97.5%) while reducing random false
switches 34 → 30.

**Held-out evaluation:**

| Measured scenario | Current engine | Current engine + n-gram veto |
|---|---:|---:|
| Correct known wrong-layout decisions | **300/300** | **288/300** |
| False switches / 720 random-key controls | 23 | **15** |
| False switches / 38 casefold abbreviations | 3 | **1** |

The validation-positive retention is **96.0%**, below the predeclared
**97%** calibration target. Thus the threshold is **NOT APPROVED**: saving
eight false switches in synthetic random noise costs twelve correct
replacements. Different natural input distributions, especially short
tokens, could make these tradeoffs substantially worse.

**Conceptual limit:** "no common n-gram" is not proof of an impossible
word. Russian allows four-consonant clusters; Hebrew spelling often omits
vowel marks; abbreviations, names and borrowings have irregular forms. An
unusual current-layout token also says nothing about whether an alternative
language has a valid word. A universal blacklist of impossible strings is
not reliable; a calibrated probabilistic signal is more defensible.

## B: dictionary expansion results

**Holdout, no directly added tokens:**

| Scenario | Baseline | Protect only | All confident | Zipf>=3 confident |
|---|---:|---:|---:|---:|
| Correct wrong-layout Wikipedia holdouts (250) | 0 | 0 | 0 | 0 |
| Correct wrong-layout wordfreq holdouts (1,200) | 0 | 0 | 0 | 0 |
| Correct established wrong-layout tokens (300) | **300** | **300** | **300** | **300** |
| False switches in 720 random-key tokens | 23 | 23 | 23 | 23 |
| False switches in 38 casefold acronyms | 3 | 3 | 3 | 3 |

**Training-source IN-SAMPLE coverage, NOT independent generalization:**

The first 120 of 1,000 added frequency words per language were used to
construct 720 positive wrong-layout cases. Results:

| Configuration | Correct / 720 added-word cases |
|---|---:|
| Baseline | 1 |
| Protect only | 0 |
| Zipf>=3 confident | 620 |
| All confident | **712** |

This is what adding entries normally achieves **for those same entries**.
It must never be reported as evidence that the new model understands
other rare words or that its actual switch precision is 98.9%. The
high-confidence mode was **not** shown safe on a diverse set of natural
negative contexts; the frozen random benchmark is small and contains many
short sequences rather than adversarial cross-layout collisions.

As expected, ordinary-only additions preserve more source text but do
not automatically authorize correction *to* the new word, because
conservative TypeBridge still requires target confidence. The sparse
improvement from names also explains why silently bulk-importing Wikipedia
titles into the confident dictionary would be unjustified.

## Outcome and deployment recommendation

1. **Do not insert** n-gram veto or external dictionaries into the live
   TypeBridge on these results. The char n-gram rule failed positive
   retention on held-out data and the lexical strategies were not proven
   beneficial outside their included words.
2. Retain separate `ordinary`, `confident` and `source_protection` evidence
   when evaluating new lexical records. Preserve private additions and
   user exclusions as overriding signals.
3. For a next corpus study, ingest POS-tagged Wiktionary entries with exact
   language, proper-name/acronym flags, lemma-vs-inflection distinction,
   source revision and attribution manifest. Validate licensing with a
   concrete commercial redistribution plan **before** packaging anything.
4. Evaluate on independent, manually audited real-word collision sets,
   typos, and native layout variants. Set false-switch and true-correction
   minimums before threshold selection. Keep training/dev/test corpora
   separate and show per-language/per-length rates.
5. No model may block the keyboard hook or infer physical No01 acceptance
   from these fixture simulations.

## Source & license references

- `https://pypi.org/project/wordfreq/` — API, RU/EN/HE support, licensing.
- `https://github.com/rspeer/wordfreq/blob/master/NOTICE.md` — attribution.
- `https://kaikki.org/dictionary/` — Wiktionary parsed resources and licenses.
- `https://kaikki.org/dictionary/Hebrew/`, `/Russian/`, `/English/` —
  downloadable structured entries, metadata and vocabulary coverage.
- `https://fasttext.cc/docs/en/language-identification.html` —
  compact original language-ID model and license.
- `https://github.com/facebookresearch/fastText/blob/main/docs/language-identification.md`
  — newer NLLB model commercial-license distinction.

**Raw result:** `build/wordfreq-ngram-lexicon-ab-heldout-20261030.json`.
Previous calibration report:
`build/wordfreq-ngram-lexicon-ab-20261030.json`.

To repeat (creates a NEW report, never overwrites a frozen result):

```powershell
python -m unittest discover -s experiments -p 'test*ngram*.py' -v
python experiments\lexicon_ngram_ab_20261030.py --output build\wordfreq-ngram-lexicon-ab-rerun.json
```

Model-only controls do not modify the EXE or settings. Results of repeat
runs on a different `wordfreq` package version are not guaranteed identical.
