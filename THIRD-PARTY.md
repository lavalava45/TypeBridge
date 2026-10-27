# Third-party code and language data

The MIT license in this repository covers TypeBridge's own source code, not
third-party dependencies or language data.

## Public dictionary preparation

`prepare_dictionaries.py` downloads word frequency tables from
[hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords), based on
[OpenSubtitles 2018](https://opus.nlpl.eu/OpenSubtitles-v2018.php).
Attribution: Hermit Dave / FrequencyWords and OpenSubtitles 2018 contributors.

FrequencyWords explicitly licenses its **content under CC BY-SA 4.0** and its
generator code under MIT. Generated/filtered frequency lists remain attributed
CC BY-SA 4.0 data; they are not relabeled MIT. Keep source attribution and license
information when redistributing those lists or a binary that bundles them.

- [Upstream license statement](https://github.com/hermitdave/FrequencyWords#license)
- [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)

The preparation script records pinned source URLs and SHA-256 hashes in
`data/SOURCES.json`. Filtering changes include Unicode normalization, script and
length checks, frequency thresholds, removal of Hebrew internal final forms,
and selection of high-frequency confidence lists. Frequency lists may still
contain errors; they are not a verified orthographic dictionary.

The already published alpha.5 ZIP includes these open dictionaries both inside
the EXE and as plain text in `data/`. The **unreleased** build preparation now
places the generated lists and verified frequency indices **only** in `data/`
beside the EXE, with attribution, source hashes, the CC BY-SA 4.0 license,
dependency notices and a matching source-code archive. Public packages are
prepared in an isolated directory using `build_public_release.py`; the private
working dictionaries are never copied into that build.

### Three-language distribution verified on 24 October 2026

The downloaded public `v0.1.0-alpha.5` ZIP already includes all three language
lists; users do not need to download dictionaries separately:

| File | Entries in alpha.5 | Data license |
| --- | ---: | --- |
| `data/ru.txt` | 212,414 | CC BY-SA 4.0 |
| `data/en.txt` | 176,155 | CC BY-SA 4.0 |
| `data/he.txt` | 185,358 | CC BY-SA 4.0 |

All three are independently prepared from the pinned FrequencyWords sources,
not copied from the local research RU/EN lists. The ZIP contains
`data/SOURCES.json`, `data/DATA-LICENSE.txt`, the full license in
`licenses/CC-BY-SA-4.0.txt`, and this attribution document. Its SHA-256 and all
53 manifest file hashes were checked. These are frequency-derived lists, not
Hunspell orthographic dictionaries; their correction decisions can differ
from the local research build. The executable and text copies carry the same
prepared data. TypeBridge's own source code remains separately MIT-licensed.

## Optional example exclusion packs

`dictionary_packs/programming.json` and `gaming.json` are filtered adaptations
of [CSpell dictionaries](https://github.com/streetsidesoftware/cspell-dicts)
at commit `6e294247ab8932b5e10573d44636e2ce5764fab3`. Their upstream source
snapshots, exact source URLs/hashes and per-package MIT copyright/license notices
are included in `dictionary_packs/`. See its README for filtering details.
These packs are opt-in and do not replace TypeBridge's main language dictionaries.

## Local research data

Earlier private development used RU/EN dictionaries extracted from a third-party application.
Redistribution rights have not been established, so those lists and binaries
containing them are excluded from this repository and public releases.
Personal dictionaries, templates, logs and historical research artifacts are
also excluded. Hspell was evaluated separately; its AGPL data and checker are
not bundled with TypeBridge.

The upstream source of the extracted Russian list has not been established.
The English list has a near-exact International Ispell reconstruction described
below; its small set of differences still needs separate treatment before
redistributing the extracted file as a whole.
This does not mean that every word or any possible upstream dictionary is
proprietary. If their original open source is established, its actual license
must be considered. Similar words, or overlap with another dictionary, do not
establish provenance of the entire extracted list.

### English provenance: near-exact International Ispell reconstruction

On 24 October 2026, a rare local entry led to the older International Ispell
English data mirrored in MiBench at commit
`0f3cbcf6b3d589a2b0753cfb9289ddf40b6b9ed8`. The comparison used the actual
word lists and affix rules, rather than a few common dictionary words:

- Input: `english.0` through `english.3`, `american.0` through `american.2`,
  `british.0` through `british.2`, and `altamer.0` through `altamer.2` in
  [the pinned English source directory](https://github.com/embecosm/mibench/tree/0f3cbcf6b3d589a2b0753cfb9289ddf40b6b9ed8/office/ispell/languages/english).
- Processing: expand the prefix/suffix flags using the supplied `english.aff`,
  including allowed cross-products, casefold, retain ASCII-letter-only tokens,
  and deduplicate. No application, keyboard hook or original program was run.
- Reconstructed set: **134,477** words. Local set: **134,485** words.
  Intersection: **134,461**, or **99.982% of the local English list**.
  There are 24 local-only entries and 16 reconstructed-only entries.
- This is strong evidence for the underlying Ispell word-list family. It does
  not establish the intermediary copy/version used by the third-party
  application or authorship of the 24 extra local entries.

The historical
[affix-file copyright and redistribution notice](https://raw.githubusercontent.com/embecosm/mibench/0f3cbcf6b3d589a2b0753cfb9289ddf40b6b9ed8/office/ispell/languages/english/english.aff)
names Geoff Kuenning and contributors. It permits redistribution and
modification subject to retaining notices/disclaimer, marking modifications,
an advertising acknowledgment, and no implied endorsement. Do not relabel
this historical version MIT or assume the newer SCOWL notice is its license.
[Ispell's Contributors file](https://github.com/embecosm/mibench/blob/0f3cbcf6b3d589a2b0753cfb9289ddf40b6b9ed8/office/ispell/Contributors)
records the history and contributors, including the origin of `english.1` and
`english.2` in dictionaries supplied by Bob Devine.

A future public Ispell-based English list should be regenerated independently
from these pinned inputs, retain the applicable notices, and explicitly review
the differences. This investigation does not replace the currently distributed
FrequencyWords English data or the local working file.

### Verified orthographic alternatives, not currently bundled

Hspell and Hunspell are different projects. Hspell supplies Hebrew linguistic
data; Hunspell supports dictionaries for many languages. Dictionary licenses
must be checked individually, separately from the checker library's license.

| Language | Verified upstream | Redistribution terms |
| --- | --- | --- |
| Russian | Alexander I. Lebedev dictionary, distributed by LibreOffice and wooorm/dictionaries | Permissive notice permits source/binary redistribution and modification; retain copyright, conditions and disclaimer, mark modifications, and do not imply author endorsement. [Full upstream notice](https://raw.githubusercontent.com/wooorm/dictionaries/8cfea406b505e4d7df52d5a19bce525df98c54ab/dictionaries/ru/license) |
| English | SCOWL-derived en_US Hunspell dictionary, distributed by wooorm/dictionaries | Upstream permits redistribution with the applicable SCOWL/Ispell and constituent-source notices. Preserve the full composite notice rather than relabeling the data MIT. [Full upstream notice](https://raw.githubusercontent.com/wooorm/dictionaries/8cfea406b505e4d7df52d5a19bce525df98c54ab/dictionaries/en/license) |
| Hebrew | Hspell | Its README explicitly applies AGPLv3 to dictionary files and generated word lists as well as code. It is not the source of our current Hebrew list and has not been substituted for it. [Upstream README](https://github.com/ivrix/hspell/blob/master/README) |

Comparison with normalized RU/EN Hunspell base entries from the pinned wooorm
snapshot did not establish identical lists. That comparison did not expand
affix rules and cannot prove or disprove historical derivation. No local
dictionary, confidence filter or public data source was replaced by this review.

## Dependencies

Runtime dependencies are listed in `requirements.txt`: psutil, pywin32 and
Pillow. PyInstaller is used for local builds. Each retains its upstream license;
refer to the installed package metadata and upstream license files. Python and
Tcl/Tk retain their respective licenses as well.

## Russian open-data candidate — 24 October 2026

`prepare_russian_dictionary.py --output-dir build/russian-open-candidate-NEW`
independently prepares a candidate from the pinned FrequencyWords source above
and Alexander I. Lebedev's Russian Hunspell data mirrored by wooorm/dictionaries
at `8cfea406b505e4d7df52d5a19bce525df98c54ab`. Each downloaded input is SHA-256
verified. The generator supports and validates the suffix-only grammar of that
snapshot; it is not a general Hunspell implementation.

The original Lebedev notice permits redistribution and modification with its
copyright, conditions and disclaimer retained, modifications marked, and no
implied author endorsement. The candidate retains that full notice and credits
Laszlo Nemeth's 2012 changes. The combined/modified frequency-derived dataset
is marked CC BY-SA 4.0, retaining both components' attributions and the original
Lebedev notice; TypeBridge's generator code remains MIT.

Generation expands suffixes before casefolding, retains Russian-letter forms
of length 1–40, and deduplicates the union. Results: 212,414 frequency entries,
1,436,553 orthographic forms, 163,955 shared entries, 1,485,012 combined entries.
The 40,008 confidence entries remain frequency-derived; orthographic membership
alone is not evidence of high frequency. Raw sources and their hashes,
transformation notes, notices and separate component word lists are retained
alongside the candidate. No extracted local Russian dictionary is used.

**Historical initial candidate check.** In 26 in-memory recognition cases,
the plain union blocked EN `zvc` → HE `זהב` because it also recognized RU
`ямс`. The orthographic word is valid; deleting it as a typo would have hidden
an ambiguity-policy problem. This candidate was **not installed at that stage**.
Subsequent frequency resolution made that case decidable without special rules.
These observations alone do not establish live-input compatibility.

### Frequency-aware, pinned public candidate (source implementation)

`prepare_frequency_indices.py` independently reads the **full** FrequencyWords
2018 tables at commit `525f9b560de45753a5ea01069454e72e9aa541c6`:
RU SHA-256 `32dfd94138aea2667ec455dcc8512029e39bd4b8ea70b09410d569b69d511a30`,
EN `7fea67ab954e2c01df6c608c9826e594cf36f8823b3243554f88245fb75dc506`,
and HE `a69a3f390eb53183bf191c4eac18282592992aef9ff184c5dcf8919f5ea0f773`.
It rejects source hash mismatch. Per-language frequencies are counts per million
of **every row's count in that unfiltered language corpus**, including tokens
excluded later by script, word length or dictionary validity checks. Unicode
NFKC, casefold and combining-mark removal are shared with the runtime. Russian
`ё` and `е` share one index key; counts for distinct original corpus rows
are summed once rather than duplicated into alternative spellings.

`prepare_open_data.py` builds a public distributable candidate using that data
and the pinned RU Hunspell snapshot, with the complete Lebedev notice in
`data/RU-DATA-LICENSE.txt`. The distribution includes `data/ru.txt`,
`data/confident_ru.txt`, EN/HE word and confidence lists,
`data/frequency_{ru,en,he}.tsv`, `data/FREQUENCY-SOURCES.json`,
`data/RU-SOURCES.json`, and associated license notices. After rate
normalization, indices are restricted to the words recognized by the
corresponding dictionary; **denominators do not change**. Their hashes are
preserved in the offline manifest for reproducible verification. **The live
TypeBridge engine no longer loads or verifies the numeric frequency tables at
startup**; only word-membership and confidence lists are read at runtime.
The previous unidentified local Russian data is not used as an input.

**Retired research policy (not active in the program):** the former 20×
frequency-ratio tie-breaker required known rates for every competing candidate
and at least 2 occurrences per million for the winner. In the historical
research case, the RU
`ямс` was excluded by the earlier high-count dictionary filter, but is
*present in the full FrequencyWords counts* at 0.174574 per million.
HE `זהב` is 50.395527 per million; the approximately 289-fold advantage
would have selected EN `zvc` → HE `זהב` under that historical policy.
The **current** choice is based instead on valid word forms, confidence,
source-word/exclusion checks and the configured target-language priority.
Statistical ratios between subtitle corpora from different languages are
heuristics, not calibrated probabilities.

**Local installation, 24 October 2026:** verified `data/` and `dist/data/`
contain the regenerated 1,485,012-line Russian dictionary, the 40,008-line
FrequencyWords-derived Russian confidence list, all three frequency tables,
`RU-SOURCES.json`, `FREQUENCY-SOURCES.json`, `SOURCES.json`, and
`RU-DATA-LICENSE.txt`, `FREQUENCY-DATA-LICENSE.txt`, `DATA-LICENSE.txt`.
The existing local EN/HE dictionaries and personal files were preserved.
The canonical `dist/TypeBridge.exe` was rebuilt and passed its six-direction
frozen self-test. Each of two isolated data variants passed 383 regression
tests; source self-test also passed. No physical input, global hook or foreground
focus test was run for this change. This **is not** a published ZIP or release.
Earlier case studies and observations remain historical.
